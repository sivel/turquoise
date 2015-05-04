#!/usr/bin/env python

import re
import bson
import logging

from functools import wraps
from happymongo import HapPyMongo
from flask.ext.github import GitHub
from flask import (Flask, session, g, request, url_for, redirect, flash,
                   render_template, abort)

try:
    from cPickle import dumps as pickle_dumps
    from cPickle import loads as pickle_loads
except ImportError:
    from pickle import dumps as pickle_dumps
    from pickle import loads as pickle_loads


app = Flask('turquoise')
app.config.from_envvar('TURQUOISE_CONFIG')

github = GitHub(app)
mongo, db = HapPyMongo(app)


@app.template_filter()
def re_pattern(value):
    try:
        return pickle_loads(value.encode('utf-8')).pattern
    except:
        return value


@github.access_token_getter
def token_getter():
    user = g.user
    if user is not None:
        return user['github_access_token']


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return wrapped


@app.before_first_request
def logger():
    app.logger.addHandler(logging.StreamHandler())
    app.logger.setLevel(logging.INFO)


@app.errorhandler(500)
def internal_server_error(e):
    app.logger.exception(e)
    return abort(500)


@app.route('/')
def index():
    return render_template('index.html', repos=app.config['GITHUB_REPOS'])


@app.route('/login')
def login():
    return github.authorize(scope='user:email')


@app.route('/login/authorized')
@github.authorized_handler
def authorized(oauth_token):
    if oauth_token is None:
        flash('Authorization failed.', 'danger')
        return redirect('index')

    user = db.users.find_one({'github_access_token': oauth_token})
    if not user:
        user = {
            'github_access_token': oauth_token,
            'regex': '',
            'files': [],
            'notified': {},
            'extra_contact': '',
        }
        details = github.get('user')
        existing = db.users.find_one({'login': details['login']})
        if not existing:
            user['_id'] = db.users.insert(user, manipulate=True)
        else:
            existing['github_access_token'] = oauth_token
            db.users.update({'_id': existing['_id']},
                            {'$set': existing})
            user = existing

    g.user = user
    details = github.get('user')
    g.user.update(details)
    db.users.update({'_id': bson.ObjectId(user['_id'])},
                    {'$set': details})

    session['user_id'] = str(user['_id'])
    return redirect(url_for('profile'))


@app.before_request
def before_request():
    g.user = None
    if 'user_id' in session:
        g.user = db.users.find_one({'_id': bson.ObjectId(session['user_id'])})


@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', repos=app.config['GITHUB_REPOS'])


@app.route('/profile/contact', methods=['POST'])
@login_required
def contact():
    partial = {
        'extra_contact': request.form.get('contact'),
        'self_notify': bool(request.form.get('self_notify'))
    }
    db.users.update({'_id': bson.ObjectId(session['user_id'])},
                    {'$set': partial})
    return redirect(url_for('profile'))


@app.route('/profile/file/add/<path:filename>')
@login_required
def file_add(filename):
    db.users.update({'_id': bson.ObjectId(session['user_id'])},
                    {'$push': {'files': filename}})
    return redirect(url_for('profile'))


@app.route('/profile/file/delete/<path:filename>')
@login_required
def file_delete(filename):
    db.users.update({'_id': bson.ObjectId(session['user_id'])},
                    {'$pull': {'files': filename}})
    return redirect(url_for('profile'))


@app.route('/profile/regex', methods=['POST'])
@login_required
def regex():
    try:
        compiled = re.compile(request.form.get('regex'))
    except re.error as e:
        db.users.update({'_id': bson.ObjectId(session['user_id'])},
                        {'$set': {'regex': request.form.get('regex')}})
        flash('Invalid regular expression: %s' % e, 'danger')
    else:
        pickled = pickle_dumps(compiled)
        db.users.update({'_id': bson.ObjectId(session['user_id'])},
                        {'$set': {'regex': pickled}})
    return redirect(url_for('profile'))


if __name__ == '__main__':
    app.run('0.0.0.0', 5000, debug=True)
