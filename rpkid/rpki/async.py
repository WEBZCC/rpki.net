"""
Utilities for event-driven programming.

$Id$

Copyright (C) 2009  Internet Systems Consortium ("ISC")

Permission to use, copy, modify, and distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND ISC DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS.  IN NO EVENT SHALL ISC BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
"""

import asyncore, signal, traceback
import rpki.log, rpki.sundial

class iterator(object):
  """Iteration construct for event-driven code.  Takes three
  arguments:

  - Some kind of iterable object

  - A callback to call on each item in the iteration

  - A callback to call after the iteration terminates.

  The item callback receives two arguments: the callable iterator
  object and the current value of the iteration.  It should call the
  iterator (or arrange for the iterator to be called) when it is time
  to continue to the next item in the iteration.

  The termination callback receives no arguments.
  """

  def __init__(self, iterable, item_callback, done_callback):
    self.item_callback = item_callback
    self.done_callback = done_callback
    try:
      self.iterator = iter(iterable)
    except:
      rpki.log.debug("Problem constructing iterator for %s" % repr(iterable))
      raise
    self()

  def __call__(self, *ignored):
    try:
      self.item_callback(self, self.iterator.next())
    except StopIteration:
      if self.done_callback is not None:
        self.done_callback()


class timer(object):
  """Timer construct for event-driven code.  It can be used in either of two ways:

  - As a virtual class, in which case the subclass should provide a
    handler() method to receive the wakup event when the timer expires; or

  - By setting an explicit handler callback, either via the
    constructor or the set_handler() method.

  Subclassing is probably more Pythonic, but setting an explict
  handler turns out to be very convenient when combined with bound
  methods to other objects.
  """

  ## @var queue
  # Timer queue, shared by all timer instances (there can be only one queue).

  queue = []

  def __init__(self, handler = None, errback = None):
    if handler is not None:
      self.set_handler(handler)
    if errback is not None:
      self.set_errback(errback)

  def set(self, when):
    """Set a timer.  Argument can be a datetime, to specify an
    absolute time, a timedelta, to specify an offset time, or None, to
    indicate that the timer should expire immediately, which can be
    useful in avoiding an excessively deep call stack.
    """
    if when is None:
      self.when = rpki.sundial.now()
    elif isinstance(when, rpki.sundial.timedelta):
      self.when = rpki.sundial.now() + when
    else:
      self.when = when
    assert isinstance(self.when, rpki.sundial.datetime)
    if self not in self.queue:
      self.queue.append(self)
    self.queue.sort()

  def __cmp__(self, other):
    return cmp(self.when, other.when)

  def cancel(self):
    """Cancel a timer, if it was set."""
    try:
      self.queue.remove(self)
    except ValueError:
      pass

  def is_set(self):
    """Test whether this timer is currently set."""
    return self in self.queue

  def handler(self):
    """Handle a timer that has expired.  This must either be overriden
    by a subclass or set dynamically by set_handler().
    """
    raise NotImplementedError

  def set_handler(self, handler):
    """Set timer's expiration handler.  This is an alternative to
    subclassing the timer class, and may be easier to use when
    integrating timers into other classes (eg, the handler can be a
    bound method to an object in a class representing a network
    connection).
    """
    self.handler = handler

  def errback(self, e):
    """Error callback.  May be overridden, or set with set_errback()."""
    rpki.log.error("Unhandled exception from timer: %s" % e)
    rpki.log.error(traceback.format_exc())

  def set_errback(self, errback):
    """Set a timer's errback.  Like set_handler(), for errbacks."""
    self.errback = errback

  @classmethod
  def runq(cls):
    """Run the timer queue: for each timer whose call time has passed,
    pull the timer off the queue and call its handler() method.
    """
    while cls.queue and rpki.sundial.now() >= cls.queue[0].when:
      t = cls.queue.pop(0)
      try:
        t.handler()
      except asyncore.ExitNow:
        raise
      except Exception, e:
        t.errback(e)

  def __repr__(self):
    return "<%s %r %r>" % (self.__class__.__name__, self.when, self.handler)

  @classmethod
  def seconds_until_wakeup(cls):
    """Calculate delay until next timer expires, or None if no timers
    are set and we should wait indefinitely.  Rounds up to avoid
    spinning in select() or poll().  We could calculate fractional
    seconds in the right units instead, but select() and poll() don't
    even take the same units (argh!), and we're not doing anything
    that hair-triggered, so rounding up is simplest.
    """
    if not cls.queue:
      return None
    now = rpki.sundial.now()
    if now >= cls.queue[0].when:
      return 0
    else:
      delay = cls.queue[0].when - now
      seconds = delay.convert_to_seconds()
      if delay.microseconds:
        seconds += 1
      return seconds

  @classmethod
  def clear(cls):
    """Cancel every timer on the queue.  We could just throw away the
    queue content, but this way we can notify subclasses that provide
    their own cancel() method.
    """
    while cls.queue:
      cls.queue.pop(0).cancel()

def _raiseExitNow(signum, frame):
  """Signal handler for event_loop()."""
  raise asyncore.ExitNow

def event_loop(catch_signals = (signal.SIGINT, signal.SIGTERM)):
  """Replacement for asyncore.loop(), adding timer and signal support."""
  try:
    old_signal_handlers = tuple((sig, signal.signal(sig, _raiseExitNow)) for sig in catch_signals)
    while asyncore.socket_map or timer.queue:
      asyncore.poll(timer.seconds_until_wakeup(), asyncore.socket_map)
      timer.runq()
  except asyncore.ExitNow:
    pass
  finally:
    for sig, handler in old_signal_handlers:
      signal.signal(sig, handler)

def exit_event_loop():
  """Force exit from event_loop()."""
  raise asyncore.ExitNow
