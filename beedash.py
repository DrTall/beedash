#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Generates a simple Beeminder dashboard displaying:
  1. Today's progress
  2. Average weekly progress for the last 2 weeks.
  3. Goal rate
  4. The ratio of 2 & 3
  5. The ratio of 2 and the prior 2 weeks (3-4 weeks ago vs 1-2 weeks ago)

Currently only hustler and drinker goals are supported.

Setup:
1. Follow instructions in secrets.py.
2. Run ./beebegone.py in a working directory it can write to.
3. Open beedash.html in a web browser.
4. Set up a cron job to run this script periodically (optional, but not
very useful otherwise).

Example Usage (in a working directory you can write to):
./beedash.py
"""

import codecs
import collections
import urllib
import json
import urllib2
from datetime import (date, datetime, timedelta)

import secrets

def epoch_time(d):
  """Seriously this is the best way to do this???"""
  return int((d - date(1970, 1, 1)).total_seconds())


TODAY = date.today()
TODAY_EPOCH = epoch_time(TODAY)
ONE_DAY = timedelta(days=1).total_seconds()
NUM_WEEKS_PER_SAMPLE = 2
SAMPLE_MIDDLE = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE)
SAMPLE_END = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE * 2)
SAMPLE_END_EPOCH = epoch_time(SAMPLE_END)

# y, m, w, d, h
RUNITS_TIMEDELTAS = {
    'y': timedelta(weeks=52),
    'm': timedelta(weeks=4),
    'w': timedelta(weeks=1),
    'd': timedelta(days=1),
    'h': timedelta(hours=1),
    }

# These symbols replace the '+' character for goals for which it doesn't quite
# make sense. For example, drinks are bad in the drinker goals, but are still
# "up" or positive in the mathematical sense.
POSITIVE_INCREMENT_SYMBOL = collections.defaultdict(lambda: '+')
POSITIVE_INCREMENT_SYMBOL.update({
    'drinker': u'▲',
    'hustler': '+',
})

# To support things like inboxer and biker, this should become a dict
# mapping onto aggregation functions. Instead of just +='ing data points
# together, we should ask what function is used to aggregate the data points
# for the goal_type in question.
#
# For now, just discard goals that don't sum.
SUPPORTED_GOAL_TYPES = POSITIVE_INCREMENT_SYMBOL.keys()

# Holy hacks Batman! The extra spaces in "red  " let us compare string lengths
# later...
COLORS = {True: "red  ", False: "green", None: "black"}


class GoalMetadata(object):
  """Tracks the data sums for a goal. The length of middle and end are
  configured in the constants above."""
  def __init__(self):
    self.today_count = 0
    # Includes today.
    self.middle_count = 0
    # Does not include middle.
    self.end_count = 0


# Per the Beeminder API
Datapoint = collections.namedtuple('Datapoint', [
    'timestamp', 'value', 'comment', 'id', 'updated_at', 'requestid',
    'daystamp', 'canonical'])

beeminder_url = 'https://www.beeminder.com/api/v1/users/me.json'
beeminder_url += ('?diff_since=%s&' % SAMPLE_END_EPOCH)  + urllib.urlencode(
    {'auth_token':secrets.BEEMINDER_AUTH_TOKEN})
user_data = json.loads(urllib2.urlopen(beeminder_url).read())

goal_metadata = collections.defaultdict(GoalMetadata)
for goal in user_data['goals']:
  points = [Datapoint(**p) for p in goal['datapoints']]
  # Convert the daystamp string into a real date object.
  points = [
      p._replace(daystamp=datetime.strptime(p.daystamp, '%Y%m%d').date())
      for p in points]

  for point in reversed(points):
    if point.daystamp == TODAY:
      goal_metadata[goal['title']].today_count += point.value
    if point.daystamp >= SAMPLE_MIDDLE:
      goal_metadata[goal['title']].middle_count += point.value
    elif point.daystamp >= SAMPLE_END and goal['initday'] < SAMPLE_END_EPOCH:
      goal_metadata[goal['title']].end_count += point.value

# Returns a pretty string number.
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
  return n

# Returns a tuple (pretty_percent, raw_percent).
def prep_percent(num, den, no_plus=False):
  if not den:
    return 'N/A', 0
  fmt = '%+.0f%%'
  if no_plus:
    fmt = fmt.replace('+', '')
  n = 100.0 * num / den
  return fmt % n, n

# Each field is a string formatted appropriately for display,
# but without any alignment applied.
DISPLAY_ROW_FIELDS = [
    'today', 'weekly', 'weekly_goal', 'percent_of_goal', 'percent_wow', 'title']
DisplayRow = collections.namedtuple('DisplayRow', DISPLAY_ROW_FIELDS)

# display_row: A GoalDisplayData object
# goal_type: One of ['hustler', 'drinker', 'biker', 'fatloser',
#                    'gainer', 'inboxer', 'drinker', 'custom']
# data_today: True iff there was a non-zero total today.
# eep: True iff this goal is eeping.
GoalDisplayData = collections.namedtuple('GoalDisplayData', [
    'display_row', 'goal_type', 'data_today', 'eep'])

# [GoalDisplayData, ...]
dipslay_data = []

for goal in user_data['goals']:
  if goal['goal_type'] not in SUPPORTED_GOAL_TYPES:
    continue

  goal_meta = goal_metadata[goal['title']]
  goal_rate = goal['rate'] or 0.0
  weekly_goal_rate = (timedelta(weeks=1).total_seconds() * goal_rate /
                      RUNITS_TIMEDELTAS[goal['runits']].total_seconds())
  # The actual rate divided by the goal rate.
  rog_pretty, rog_raw = prep_percent(
      goal_meta.middle_count / NUM_WEEKS_PER_SAMPLE,
      weekly_goal_rate, no_plus=True)
  rog_pretty = '<font color="%s">%s</font>' % (
      COLORS[None if abs(100 - rog_raw) < 10 else
             (goal['goal_type'] == 'hustler') != (rog_raw > 100)], rog_pretty)

  # The middle count divided by the end count.
  wow_pretty, wow_raw = prep_percent(
      goal_meta.middle_count - goal_meta.end_count, goal_meta.end_count)
  wow_pretty = '<font color="%s">%s</font>' % (
      COLORS[None if abs(wow_raw) < 10 else
             (goal['goal_type'] == 'hustler') != (wow_raw > 0)], wow_pretty)

  dipslay_data.append(GoalDisplayData(
      DisplayRow(
          prep_number(goal_meta.today_count),
          prep_number(goal_meta.middle_count / NUM_WEEKS_PER_SAMPLE),
          prep_number(weekly_goal_rate),
          rog_pretty,
          wow_pretty,
          goal['title']),
      goal['goal_type'],
      bool(goal_meta.today_count),
      goal['losedate'] > TODAY_EPOCH and
      goal['losedate'] - TODAY_EPOCH < 2 * ONE_DAY,
      ))

# Compute the maximum length for each field in all DisplayRows.
maximum_lengths = {
    i: max(len(d.display_row[i]) for d in dipslay_data)
    for i in range(len(DISPLAY_ROW_FIELDS))}

result = ['<meta charset="UTF-8"><html><body><font face=monaco>']
first_grey_found = False
for data in sorted(dipslay_data, key=lambda d: (
    not d.data_today, -ord(d.goal_type[0]), d.display_row.title)):
  line = '%s today %s weekly vs %s (%s of goal, %s w/w) %s<br>' % tuple(
      # Hacks because ljust won't accept a string as an input...
      element + '&nbsp;' * (maximum_lengths[index] - len(element))
      for index, element in enumerate(data.display_row))
  if not data.data_today:
    if not first_grey_found:
      result.append('<br><br>')
      first_grey_found = True
    # This is a hack to avoid nesting font tags by replacing existing ones with
    # spans. Should I be using CSS? Probably.
    line = '<font color="grey">%s</font>' % line.replace('font', 'span')
  line = line.replace('+', POSITIVE_INCREMENT_SYMBOL[data.goal_type])
  if data.eep:
    line = '<span style="background-color:#ff9900;">%s</span>' % line
  result.append(line)

result.append('<br><br>Updated: %s' % datetime.now())
result.append('</body></html>')

with codecs.open('beedash.html', 'w', 'utf-8') as f:
  f.write(u'\n'.join(result))

print 'Beedash ran successfully at %s' % datetime.now()
