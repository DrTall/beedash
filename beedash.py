#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Searches your Gmail inbox for emails that look like they're from the
Beeminder Bot and archives them if the goal they're nagging you about has
data newer than the reminder.

This script only checks the datestamp on goal data. You might still be about
to derail the goal, so you might need to be careful if it's an eep day and
you've put in some data but not enough to give a safe day.

Setup:
1. pip install google-api-python-client
2. Follow instructions in secrets.py.
3. Run ./beebegone.py in a working directory it can write to (for caching
the Gmail credentials) and authorize the app in the web browser. Future runs
won't require human interaction unless you delete your gmail.storage
credentials.
4. Set up a cron job to run this script periodically (optional, but not
very useful otherwise).

Example Usage (in a working directory you can write to):
./beebegone.py
"""

import codecs
import collections
import httplib2
import re
import urllib
import json
import urllib2
from datetime import (date, datetime, timedelta, time)

import secrets

def epoch_time(d):
  """Seriously this is the best way to do this???"""
  return int((d - date(1970,1,1)).total_seconds())

TODAY = date.today()
ONE_DAY = timedelta(days=1).total_seconds()
NUM_WEEKS_PER_SAMPLE = 2
SAMPLE_MIDDLE = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE)
SAMPLE_END = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE * 2)
SAMPLE_END_EPOCH = epoch_time(SAMPLE_END)

class GoalMetadata:
  def __init__(self):
    self.today_count = 0
    # Includes today.
    self.middle_count = 0
    # Does not include middle.
    self.end_count = 0

Datapoint = collections.namedtuple('Datapoint', ['timestamp', 'value', 'comment', 'id', 'updated_at', 'requestid', 'daystamp', 'canonical'])

beeminder_url = 'https://www.beeminder.com/api/v1/users/me.json'
beeminder_url += ('?diff_since=%s&' % SAMPLE_END_EPOCH)  + urllib.urlencode(
    {'auth_token':secrets.BEEMINDER_AUTH_TOKEN})
user_data = json.loads(urllib2.urlopen(beeminder_url).read())

goal_metadata = collections.defaultdict(GoalMetadata)
for goal in user_data['goals']:
  points = [Datapoint(**p) for p in goal['datapoints']]
  points = [p._replace(daystamp = datetime.strptime(p.daystamp, '%Y%m%d').date()) for p in points]

  for point in reversed(points):
    if point.daystamp == TODAY:
      goal_metadata[goal['title']].today_count += point.value
    if point.daystamp >= SAMPLE_MIDDLE:
      goal_metadata[goal['title']].middle_count += point.value
    elif point.daystamp >= SAMPLE_END and goal['initday'] < SAMPLE_END_EPOCH:
      goal_metadata[goal['title']].end_count += point.value

def prep_number(n):
  fmt = '%+.02f'
  if abs(n) > 10000:
    fmt = '%+.01f'
  if abs(n) > 1000:
    n /= 1000.0
    fmt += 'K'
  elif abs(n) > 100:
    fmt = '%+.0f'
  n = fmt % n
  n = n.ljust(6)
  n = n.replace(' ', '&nbsp;')
  return n

def prep_percent(num, den, no_plus=False):
  if not den:
    return 'N/A&nbsp;&nbsp;'
  fmt = '%+.0f%%'
  if no_plus:
    fmt = fmt.replace('+', '')
  n = fmt % (100.0 * num / den)
  n = n.ljust(5)
  n = n.replace(' ', '&nbsp;')
  return n

# y, m, w, d, h
RUNITS_TIMEDELTAS = {
    'y': timedelta(weeks=52),
    'm': timedelta(weeks=4),
    'w': timedelta(weeks=1),
    'd': timedelta(days=1),
    'h': timedelta(hours=1),
    }

result = ['<meta charset="UTF-8"><html><body><font face=monaco>']
for zero_inverter in [True, False]:
  for goal_type in ['hustler', 'drinker']: #, 'biker', 'fatloser', 'gainer', 'inboxer', 'drinker', 'custom']:
    for goal in sorted(user_data['goals'], key=lambda g: g['title']):
      title = goal['title']
      if (not goal_metadata[title].today_count) == zero_inverter:
        continue
      if goal['goal_type'] != goal_type:
        continue
      wow = prep_percent(goal_metadata[title].middle_count - goal_metadata[title].end_count, goal_metadata[title].end_count)
      colors = {True: "red", False: "green"}
      if '-' in wow[:2]:
        wow = wow.replace(' ', '&nbsp;')
        wow = '<font color="%s">%s</font>' % (colors[goal['goal_type'] == 'hustler'], wow)
      elif '+' in wow[:2]:
        wow = wow.replace(' ', '&nbsp;')
        wow = '<font color="%s">%s</font>' % (colors[goal['goal_type'] != 'hustler'], wow)
      else:
        wow = wow.replace(' ', '&nbsp;')
      today = prep_number(goal_metadata[title].today_count)
      week = prep_number(goal_metadata[title].middle_count / NUM_WEEKS_PER_SAMPLE)
      two_weeks = prep_number(goal_metadata[title].end_count / NUM_WEEKS_PER_SAMPLE)
      goal_rate = goal['rate'] or 0.0
      weekly_goal_rate = timedelta(weeks=1).total_seconds() * goal_rate / RUNITS_TIMEDELTAS[goal['runits']].total_seconds()
      rate = prep_number(weekly_goal_rate)
      gor = prep_percent(goal_metadata[title].middle_count / NUM_WEEKS_PER_SAMPLE, weekly_goal_rate, no_plus=True)
      if gor.find('%') < 3:
        gor = '<font color="%s">%s</font>' % (colors[goal['goal_type'] == 'hustler'], gor)
      else:
        gor = '<font color="%s">%s</font>' % (colors[goal['goal_type'] != 'hustler'], gor)

      line ='%s today %s weekly vs %s (%s of goal, %s w/w) %s<br>' % (today, week, rate, gor, wow, title)
      if not zero_inverter:
        line = '<font color="grey">%s</font>' % line.replace('font', 'span')
      if goal['goal_type'] == 'drinker':
        line = line.replace('+', u'▲')
      result.append(line)
  result.append('<br><br>')

result.append('<br><br>Updated: %s' % datetime.now())
result.append('</body></html>')

with codecs.open('beedash.html', 'w', 'utf-8') as f:
  f.write(u'\n'.join(result))

print 'Beedash ran successfully at %s' % datetime.now()