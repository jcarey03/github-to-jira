#!/usr/bin/env python

"""
Adapted from https://github.com/cnorthwood/github-to-jira.
"""

import csv
import itertools
import itertools as it
import os
import subprocess
import sys
import urllib2
from time import sleep

import simplejson
from dateutil.parser import parse as dateparse

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_REPO = os.getenv('GITHUB_REPO')
GITHUB_API = 'https://api.github.com/repos/' + GITHUB_REPO
GITHUB_ISSUES_LIST = GITHUB_API + "/issues"
GITHUB_ISSUES_COMMENTS = GITHUB_ISSUES_LIST + "/%s/comments"

# e.g., '[bot]'
# issues referencing these user prefixes will be filtered out
ignore_user_prefixes = {}

# e.g., 'jcarey03'
# issues referencing these users will be filtered out
inactive_github_users = {
}

# e.g., 'jcarey03': 'jcarey'
# mapping of github username to jira username
# any user that is not in inactive_github_users must have a mapping
github_user_to_jira_user = {
}


def github_open_api(call):
    request = urllib2.Request(call)
    request.add_header('Authorization', 'token ' + GITHUB_TOKEN)
    return urllib2.urlopen(request)


def github_api_call(call):
    """
    Make a call to the Github API
    """
    try:
        return simplejson.load(github_open_api(call))
    except urllib2.HTTPError as e:
        if e.code == 403:
            # hit the rate limit - wait 60 seconds then retry
            print >> sys.stderr, "Hit the rate limit, waiting 60 seconds..."
            sleep(60)
            return github_api_call(call)
        else:
            raise


def get_num_comments(issue):
    return issue['_comments']


def get_num_labels(issue):
    return len(issue['Labels'])


def get_num_watchers(issue):
    return len(issue['Watchers'])


def get_comments(issue):
    comments = []
    data = github_api_call(GITHUB_ISSUES_COMMENTS % issue['_id'])
    if len(data) > 0:
        for comment in data:
            if is_valid_user(comment['user']):
                comment_header = format_datetime(dateparse(comment['created_at'])) + ';' + \
                                 github_user_to_jira_user[comment['user']['login']] + ';'
                comments.append({
                    'Comment Body': comment_header + convert_markdown(comment['body']),
                })
    return comments


def is_valid_user(user):
    return not (
        any([prefix in user['login'] for prefix in ignore_user_prefixes]) or
        user['login'] in inactive_github_users
    )


def get_labels(issue):
    return issue['Labels']


def get_assignee(user):
    if user and is_valid_user(user):
        return github_user_to_jira_user[user['login']]


def get_watchers(assignees):
    watchers = []
    for assignee in assignees or []:
        if is_valid_user(assignee):
            watchers.append(github_user_to_jira_user[assignee['login']])
    return watchers


def load_github_issues():
    issues = {}
    for page_idx in range(1, 10000):
        api_call = GITHUB_ISSUES_LIST + '?per_page=100&page=%s&state=all' % page_idx
        print "Fetching page %d..." % page_idx
        data = github_api_call(api_call)
        if len(data) == 0:
            print('Breaking on page %d' % page_idx)
            break
        for issue in data:
            if is_valid_user(issue['user']) and 'pull_request' not in issue:
                issue_id = '[%s|%s]' % (issue['html_url'].split('/')[-1], issue['html_url'])
                issues[issue_id] = {
                    'ID': issue_id,
                    'Summary': issue['title'],
                    'Description': convert_markdown(issue['body']),
                    'Labels': map(lambda x: x['name'].replace(' ', '_'), issue['labels'] or []),
                    'Reporter': github_user_to_jira_user[issue['user']['login']],
                    'Assignee': get_assignee(issue['assignee']),
                    'Watchers': get_watchers(issue['assignees']),
                    'Date Created': format_datetime(dateparse(issue['created_at'])),
                    'Date Modified': format_datetime(dateparse(issue['updated_at'])),
                    'Date Resolved': format_datetime(dateparse(issue['closed_at'])) if issue.get('closed_at') else None,
                    'Status': 'Closed' if issue.get('closed_at') else 'Open',
                    'Resolution': 'Done' if issue.get('closed_at') else 'Unresolved',
                    '_comments': issue['comments'],
                    '_id': issue['number'],
                }
    return issues


def format_datetime(dt):
    return dt.strftime('%Y/%m/%d %H:%M')


def ensure_encoded(obj, encoding='us-ascii'):
    """
    If a string is unicode return its encoded version, otherwise return it raw.
    """
    if isinstance(obj, unicode):
        return obj.encode(encoding)
    else:
        return obj


def pad_list(l, size, obj):
    """
    Pad a list to given size by appending the object repeatedly as necessary.
    Cuts off the end of the list if it is longer than the supplied size.

    >>> pad_list(range(4), 6, 'x')
    [0, 1, 2, 3, 'x', 'x']
    >>> pad_list(range(4), 2, 'x')
    [0, 1]
    >>> pad_list(range(4), 4, 'x')
    [0, 1, 2, 3]

    """
    return list(it.islice(it.chain(l, it.repeat(obj)), size))


def convert_markdown(gh_markdown):
    if gh_markdown:
        p = subprocess.Popen(
            [
                '/Users/jcarey/.pyenv/shims/python',
                '/Users/jcarey/src/mistletoe/mistletoe/main_converter.py'
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={'PYTHONPATH': '/Users/jcarey/src/mistletoe'},
        )
        output, err = p.communicate(gh_markdown.encode('utf-8'))
        if err:
            raise Exception('Failed markdown conversion: %s' % err)
        return output.rstrip()


def write_jira_csv(fd):
    issues = load_github_issues().values()
    issue_writer = csv.writer(fd)
    max_num_labels = max(map(get_num_labels, issues))
    max_num_watchers = max(map(get_num_watchers, issues))
    max_num_comments = max(map(get_num_comments, issues))
    label_headers = ['Labels' for _ in xrange(max_num_labels)]
    watcher_headers = ['Watchers' for _ in xrange(max_num_watchers)]
    comment_headers = ['Comment Body' for _ in xrange(max_num_comments)]
    single_field_headers = [
        'ID',
        'Summary',
        'Description',
        'Reporter',
        'Assignee',
        'Date Created',
        'Date Modified',
        'Date Resolved',
        'Status',
        'Resolution'
    ]
    headers = []
    headers += single_field_headers
    headers += label_headers
    headers += watcher_headers
    headers += comment_headers
    issue_writer.writerow(headers)
    issue_idx = 1
    for issue in issues:
        print "Processing issue [%d/%d]..." % (issue_idx, len(issues))
        issue_idx += 1

        row = [issue[header] for header in single_field_headers]
        row += pad_list(get_labels(issue), max_num_labels, '')
        row += pad_list(issue['Watchers'], max_num_watchers, '')
        comments = [[comment['Comment Body']] for comment in get_comments(issue)]
        row += pad_list(list(itertools.chain(*comments)), max_num_comments, '')
        row = [ensure_encoded(e, 'utf-8') for e in row]
        issue_writer.writerow(row)


if __name__ == '__main__':
    with open(sys.argv[1], 'w') as fd:
        write_jira_csv(fd)
