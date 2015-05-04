#!/usr/bin/env python

from gevent import monkey
from gevent.pool import Pool
monkey.patch_all()  # noqa

import os
import imp
import requests

from github import Github
from fnmatch import fnmatch
from happymongo import HapPyMongo
from collections import defaultdict
from github.PullRequest import PullRequest
from github.GithubObject import _ValuedAttribute
from jinja2 import Environment, FileSystemLoader

try:
    from cPickle import loads as pickle_loads
except ImportError:
    from pickle import loads as pickle_loads


def unpickle(value):
    try:
        return pickle_loads(value.encode('utf-8'))
    except:
        return value


class TurquoiseBreak(Exception):
    pass


class Turquoise(object):
    def __init__(self, config_file=None, config_envvar='TURQUOISE_CONFIG'):
        self._config = {}
        self._config_file = config_file
        self._config_envvar = config_envvar

        loader = FileSystemLoader(os.path.join(os.path.dirname(__file__),
                                  'templates'))
        self._jinja = Environment(loader=loader, trim_blocks=True)

        _, self._db = HapPyMongo(self.config)

    @property
    def config(self):
        if self._config:
            return self._config

        rv = self._config_file or os.environ.get(self._config_envvar)
        if not rv:
            raise SystemExit('The environment variable %r is not set '
                             'or no config file was provided '
                             'and as such configuration could not be '
                             'loaded.  Set this variable and make it '
                             'point to a configuration file' %
                             self._config_envvar)

        try:
            obj = imp.load_source('config', rv)
        except IOError as e:
            e.strerror = 'Unable to load configuration file (%s)' % e.strerror
            raise SystemExit(e)

        for key in dir(obj):
            if key.isupper():
                self._config[key] = getattr(obj, key)
        return self._config

    def _fetch_repo(self, repo, results):
        for issue in repo.get_issues():
            details = {
                'repo': repo.full_name,
                'number': issue.number,
                'url': issue.html_url,
                'title': issue.title,
                'assignee': (issue.assignee.login if issue.assignee
                             else None),
                'user': issue.user.login,
                'blobs': [issue.title, issue.body],
                'files': [],
                'type': 'issue',
            }

            # for comment in issue.get_comments():
            #     details['blobs'].append(comment.body)

            if issue.pull_request:
                details['type'] = 'pull request'

                pull = PullRequest(repo._requester, {}, {}, completed=True)
                pull._url = _ValuedAttribute('%s/%s/%s' % (repo.url, 'pulls',
                                                           issue.number))

                for pull_file in pull.get_files():
                    details['files'].append(pull_file.filename)

                # for comment in pull.get_comments():
                #     details['blobs'].append(comment.body)

                # I'm not totally sure this is useful, so disabling as it
                # get's us back a lot of API requests
                # for commit in pull.get_commits():
                #     details['blobs'].append(commit.commit.message)

            results[repo.full_name].append(details)

    def scan(self):
        g = Github(client_id=self.config['GITHUB_CLIENT_ID'],
                   client_secret=self.config['GITHUB_CLIENT_SECRET'],
                   per_page=100)

        results = defaultdict(list)

        if not isinstance(self.config['GITHUB_REPOS'], list):
            repos = [self.config['GITHUB_REPOS']]
        else:
            repos = self.config['GITHUB_REPOS']

        pool = Pool(size=5)
        for repo_name in repos:
            repo = g.get_repo(repo_name)
            pool.spawn(self._fetch_repo, repo, results)
        pool.join()

        return results

    def notify(self, user, item, match):
        repo = 'notified.%s' % item['repo']
        self._db.users.update({'_id': user['_id']},
                              {'$push': {repo: item['number']}})

        template = self._jinja.get_template('notification.txt')
        body = template.render(user=user, match=match, item=item)

        mailgun_domain = self.config['MAILGUN_DOMAIN']
        default_subject = '[GitHub Notifier] New Event - %(number)s'
        try:
            subject = self.config.get('EMAIL_SUBJECT',
                                      default_subject) % item
        except TypeError:
            subject = self.config.get('EMAIL_SUBJECT',
                                      default_subject)

        addresses = [
            user['email']
        ]
        try:
            if user['extra_contact']:
                addresses.append(user['extra_contact'])
        except KeyError:
            pass

        return requests.post(
            'https://api.mailgun.net/v2/%s/messages' % mailgun_domain,
            auth=('api', self.config['MAILGUN_API_KEY']),
            data={
                'from': self.config['EMAIL_FROM'],
                'to': addresses,
                'subject': subject,
                'text': body
            }
        )

    def match(self, results=None):
        if results is None:
            raise SystemExit('No results provided, please provide the results '
                             'from Turquoise.scan')

        users = self._db.users.find()
        for user in users:
            regex = unpickle(user['regex'])
            files = user['files']
            notified = user['notified']
            for repo, items in results.iteritems():
                for item in items:
                    if not user['self_notify'] and item['user'] == user['login']:
                        continue

                    if item['number'] in notified.get(item['repo'], []):
                        continue

                    try:
                        for item_file in item['files']:
                            for file_pat in files:
                                if fnmatch(item_file, file_pat):
                                    self.notify(user, item, file_pat)
                                    raise TurquoiseBreak()
                    except TurquoiseBreak:
                        continue

                    if not isinstance(regex, basestring):
                        try:
                            for blob in item['blobs']:
                                if regex.search(blob):
                                    self.notify(user, item, regex.pattern)
                                    raise TurquoiseBreak()
                        except (TurquoiseBreak, TypeError):
                            continue


def main():
    turquoise = Turquoise()
    results = turquoise.scan()
    turquoise.match(results)


if __name__ == '__main__':
    main()
