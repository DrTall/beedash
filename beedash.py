#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Generates a simple Beeminder dashboard displaying:
  1. Today's progress
  2. Average weekly progress for the last 4 weeks.
  3. Goal rate
  4. The ratio of 2 & 3
  5. The ratio of 2 and the prior 4 weeks (5-8 weeks ago vs 1-4 weeks ago)

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

import ast
import codecs
import collections
import urllib
import json
import urllib2
from datetime import (date, datetime, timedelta)
from slackclient import SlackClient

import secrets

def epoch_time(d):
  """Seriously this is the best way to do this???"""
  return int((d - date(1970, 1, 1)).total_seconds())


TODAY = date.today()
TODAY_EPOCH = epoch_time(TODAY)
ONE_DAY = timedelta(days=1).total_seconds()
NUM_WEEKS_PER_SAMPLE = 4
SAMPLE_MIDDLE = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE)
SAMPLE_END = TODAY - timedelta(weeks=NUM_WEEKS_PER_SAMPLE * 2)
SAMPLE_END_EPOCH = epoch_time(SAMPLE_END)

VACAY_HACK_END = date(2016, 1, 25)
VACAY_HACK_EPOCH_END = epoch_time(VACAY_HACK_END)
VACAY_HACK_START = VACAY_HACK_END - timedelta(days=3)
VACAY_HACK_EPOCH_START = epoch_time(VACAY_HACK_START)

# y, m, w, d, h
RUNITS_TIMEDELTAS = {
    'y': timedelta(weeks=52),
    'm': timedelta(weeks=4),
    'w': timedelta(weeks=1),
    'd': timedelta(days=1),
    'h': timedelta(hours=1),
    }


class HustlerAggregator(object):
  def __init__(self):
    self.total = 0

  def record(self, point):
    self.total += point

  def record_prior_period(self, _):
    pass

  def delta(self):
    return self.total


class InboxerAggregator(object):
  def __init__(self):
    self.initial = None
    self.final = None

  def record(self, point):
    if self.initial == None:
      self.initial = point
    self.final = point

  def record_prior_period(self, point):
    self.initial = point

  def delta(self):
    return (0 if self.initial is None or self.final is None else
            self.final - self.initial)


class BikerAggregator(object):
  def __init__(self):
    self.initial = None
    self.final = None
    self.accumulated = 0

  def record(self, point):
    if self.initial == None:
      self.initial = point
    if point == 0:
      self.accumulated = 0 if self.final is None else self.final
    self.final = self.accumulated + point

  def record_prior_period(self, point):
    self.initial = point

  def delta(self):
    return (0 if self.initial is None or self.final is None else
            self.final - self.initial)


def get_goal_aggregator(goal):
  if goal['odom']:
    return BikerAggregator
  if goal['aggday'] == 'sum':
    return HustlerAggregator
  return InboxerAggregator


def substitute_do_less_symbols(string):
  """Replace the +/- symbols with up/down triangles for yaw!=dir goals.

  These symbols set WEEN/RASH goals apart from PHAT/MOAR goals. If you see
  +/-, you want the absolute value to be as big as possible. If you see the
  triangles, you want the absolute value to be as small as possible.
  """
  return string.replace('+', u'▲').replace('-', u'▼')



# Holy hacks Batman! The extra spaces in "red  " let us compare string lengths
# later...
COLORS = {True: "red  ", False: "green", None: "black"}


class GoalMetadata(object):
  """Tracks the data sums for a goal. The length of middle and end are
  configured in the constants above."""
  def __init__(self, aggregator_constructor):
    self.today_count = aggregator_constructor()
    # Includes today.
    self.middle_count = aggregator_constructor()
    # Does not include middle.
    self.end_count = aggregator_constructor()


# Per the Beeminder API
Datapoint = collections.namedtuple('Datapoint', [
    'timestamp', 'value', 'comment', 'id', 'updated_at', 'requestid',
    'daystamp', 'canonical'])

goals = []
lint_violations = set()
for auth_token in secrets.BEEMINDER_AUTH_TOKENS:
  beeminder_url = 'https://www.beeminder.com/api/v1/users/me.json'
  beeminder_url += ('?diff_since=%s&' % SAMPLE_END_EPOCH)  + urllib.urlencode(
      {'auth_token':auth_token})
  user_data = json.loads(urllib2.urlopen(beeminder_url).read())
  goals.extend(user_data['goals'])
  del user_data

goal_metadata = {}
for goal in goals:
  points = [Datapoint(**p) for p in goal['datapoints']]
  # Convert the daystamp string into a real date object.
  points = [
      p._replace(daystamp=datetime.strptime(p.daystamp, '%Y%m%d').date())
      for p in points]

  if goal['slug'] == secrets.BEELINT_GOAL_NAME and points:
    lint_violations = set(points[-1].comment.split(','))
    if not any(lint_violations):
      lint_violations = set()
    print 'Found the Beelint goal! lint_violations = %s' % lint_violations


  meta = GoalMetadata(get_goal_aggregator(goal))
  for point in points:
    if point.daystamp == TODAY:
      meta.today_count.record(point.value)
    elif point.daystamp >= SAMPLE_MIDDLE:
      meta.today_count.record_prior_period(point.value)
      meta.middle_count.record(point.value)
    elif point.daystamp >= SAMPLE_END and goal['initday'] < SAMPLE_END_EPOCH:
      meta.today_count.record_prior_period(point.value)
      meta.middle_count.record_prior_period(point.value)
      meta.end_count.record(point.value)
  goal_metadata[goal['title']] = meta

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

GoalDisplayData = collections.namedtuple('GoalDisplayData', [
    'display_row', 'goal', 'goal_meta'])

# [GoalDisplayData, ...]
dipslay_data = []

for goal in goals:
  goal_meta = goal_metadata[goal['title']]
  goal_rate = goal['mathishard'][2] or 0.0
  weekly_goal_rate = (timedelta(weeks=1).total_seconds() * goal_rate /
                      RUNITS_TIMEDELTAS[goal['runits']].total_seconds())

  # Hide the Beelint goal since we know it is updated daily and is not
  # very interesting by itself.
  if goal['slug'] == secrets.BEELINT_GOAL_NAME:
    continue

  # The actual rate divided by the goal rate.
  rog_pretty, rog_raw = prep_percent(
      (goal_meta.middle_count.delta() + goal_meta.today_count.delta()) /
      NUM_WEEKS_PER_SAMPLE,
      weekly_goal_rate, no_plus=True)
  rog_pretty = '<font color="%s">%s</font>' % (
      COLORS[None if abs(100 - rog_raw) < 10 or not weekly_goal_rate else
             (goal['yaw'] * goal['dir'] == 1) != (rog_raw > 100)], rog_pretty)

  # The middle count divided by the end count.
  wow_denom = (0 if goal['initday'] > SAMPLE_END_EPOCH
               else goal_meta.end_count.delta())
  wow_pretty, wow_raw = prep_percent(
      goal_meta.middle_count.delta() + goal_meta.today_count.delta() -
      goal_meta.end_count.delta(),
      wow_denom,
      )
  wow_pretty = '<font color="%s">%s</font>' % (
      COLORS[None if abs(wow_raw) < 10 else
             (goal['yaw'] * goal['dir'] == 1) != (wow_raw > 0)], wow_pretty)

  dipslay_data.append(GoalDisplayData(
      DisplayRow(
          prep_number(goal_meta.today_count.delta()),
          prep_number((goal_meta.middle_count.delta() +
                       goal_meta.today_count.delta()) / NUM_WEEKS_PER_SAMPLE),
          prep_number(weekly_goal_rate),
          rog_pretty,
          wow_pretty,
          goal['title']),
      goal,
      goal_meta,
      ))
del goal

# Compute the maximum length for each field in all DisplayRows.
maximum_lengths = {
    i: max(len(d.display_row[i]) for d in dipslay_data)
    for i in range(len(DISPLAY_ROW_FIELDS))}

result = ['<meta charset="UTF-8"><html><body><font face=monaco>']
first_grey_found = False
current_eep_goals = set()
for data in sorted(dipslay_data, key=lambda d: (
    not d.goal_meta.today_count.delta(),
    -ord(d.goal['goal_type'][0]),
    d.display_row.title)):
  line = '%s today %s weekly vs %s (%s of goal, %s m/m) %s<br>' % tuple(
      # Hacks because ljust won't accept a string as an input...
      element + '&nbsp;' * (maximum_lengths[index] - len(element))
      for index, element in enumerate(data.display_row))
  if not data.goal_meta.today_count.delta():
    if not first_grey_found:
      result.append('<br><br>')
      first_grey_found = True
    # This is a hack to avoid nesting font tags by replacing existing ones with
    # spans. Should I be using CSS? Probably.
    line = '<font color="grey">%s</font>' % line.replace('font', 'span')
  if data.goal['dir'] != data.goal['yaw']:
    line = substitute_do_less_symbols(line)
  # Is it an eep!?
  else:
    if (data.goal['losedate'] > TODAY_EPOCH and
        data.goal['losedate'] - TODAY_EPOCH < 2 * ONE_DAY):
      line = '<span style="background-color:#ff9900;">%s</span>' % line
      current_eep_goals.add(data.goal['slug'])
    elif data.goal['slug'] in lint_violations or (
        VACAY_HACK_EPOCH_START - TODAY_EPOCH <= 10 * ONE_DAY and data.goal['losedate'] <= VACAY_HACK_EPOCH_END):
      line = '<span style="background-color:#ffc0cb;">%s</span>' % line
  result.append(line)

result.append('<br><br>Updated: %s' % datetime.now())
result.append('</body></html>')

with codecs.open(secrets.DASHBOARD_PATH, 'w', 'utf-8') as f:
  f.write(u'\n'.join(result))

if secrets.SLACK_AUTH_TOKEN:
  print 'Currently eep!ing goals: %s' % current_eep_goals
  def slack_msg_eep(slug, eep_type):
      sc.api_call(
        'chat.postMessage',
        channel=secrets.SLACK_CHAN_ID,
        username='Beeminder Bot',
        icon_url='https://avatars.slack-edge.com/2015-10-26/13284050673_1c5942e75748a08869cf_48.jpg',
        text="Eep! %s's Beeminder goal <http://www.beeminder.com/%s/goals/%s|%s> is %s!" % (
            secrets.SLACK_BEEMINDER_FRIENDLY_NAME, secrets.SLACK_BEEMINDER_USERNAME, slug, slug, eep_type))
  def slack_msg_adios(slug, eep_type, num_left):
      left_str = '%s more eep!s left today.' % num_left if num_left else 'No more eep!s left today! Hooray!'
      sc.api_call(
        'chat.postMessage',
        channel=secrets.SLACK_CHAN_ID,
        username='Beeminder Bot',
        icon_url='https://avatars.slack-edge.com/2015-10-26/13284050673_1c5942e75748a08869cf_48.jpg',
        text="Whew, %s's Beeminder goal <http://www.beeminder.com/%s/goals/%s|%s> is no longer %s!\n%s" % (
            secrets.SLACK_BEEMINDER_FRIENDLY_NAME, secrets.SLACK_BEEMINDER_USERNAME, slug, slug, eep_type, left_str))


  sc = SlackClient(secrets.SLACK_AUTH_TOKEN)
  if not sc.rtm_connect():
      print "Slack connection failed!"
  else:
    old_eep_goals, old_lint_goals = set(), set()
    try:
      with open('eeps.txt') as f:
        old_eep_goals, old_lint_goals = ast.literal_eval(f.read())
        old_eep_goals, old_lint_goals = set(old_eep_goals), set(old_lint_goals)
    except Exception as e:
      print e
    print 'Previously eep!ing goals: %s' % old_eep_goals
    new_eeps = current_eep_goals - old_eep_goals
    new_lints = lint_violations - old_lint_goals
    adios_eeps = old_eep_goals - current_eep_goals
    adios_lints = old_lint_goals - lint_violations
    print 'New eeps: %s , adios eeps: %s' % (new_eeps, adios_eeps)
    print 'New lints: %s , adios lints: %s' % (new_lints, adios_lints)

    with open('eeps.txt', 'w') as f:
      f.write(str((list(current_eep_goals), list(lint_violations))))
    for slug in new_eeps:
      slack_msg_eep(slug, 'red')
    for slug in new_lints:
      slack_msg_eep(slug, 'pink')
    for slug in adios_eeps:
      slack_msg_adios(slug, 'red', len(current_eep_goals))
    for slug in adios_lints:
      slack_msg_adios(slug, 'pink', len(lint_violations))

print 'Beedash ran successfully at %s' % datetime.now()
