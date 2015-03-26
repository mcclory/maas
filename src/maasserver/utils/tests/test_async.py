# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for asynchronous utilities."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from functools import partial
from textwrap import dedent
import threading
from time import time

import crochet
from crochet import wait_for_reactor
from maasserver.exceptions import IteratorReusedError
from maasserver.testing.orm import PostCommitHooksTestMixin
from maasserver.utils import async
from maasserver.utils.async import DeferredHooks
from maastesting.factory import factory
from maastesting.matchers import (
    IsFiredDeferred,
    IsUnfiredDeferred,
    MockCallsMatch,
)
from maastesting.testcase import MAASTestCase
from mock import (
    call,
    Mock,
    sentinel,
)
from provisioningserver.rpc.testing import TwistedLoggerFixture
from testtools.deferredruntest import extract_result
from testtools.matchers import (
    Contains,
    Equals,
    HasLength,
    Is,
    IsInstance,
    LessThan,
)
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.task import deferLater
from twisted.python.failure import Failure
from twisted.python.threadable import isInIOThread

# Ensure that the reactor is running; one or more tests need it.
crochet.setup()


class TestGather(MAASTestCase):

    def test_gather_nothing(self):
        time_before = time()
        results = list(async.gather([], timeout=10))
        time_after = time()
        self.assertThat(results, Equals([]))
        # gather() should return well within 9 seconds; this shows
        # that the call is not timing out.
        self.assertThat(time_after - time_before, LessThan(9))


class TestGatherScenarios(MAASTestCase):

    scenarios = (
        ("synchronous", {
            # Return the call as-is.
            "wrap": lambda call: call,
        }),
        ("asynchronous", {
            # Defer the call to a later reactor iteration.
            "wrap": lambda call: partial(deferLater, reactor, 0, call),
        }),
    )

    def test_gather_from_calls_without_errors(self):
        values = [
            self.getUniqueInteger(),
            self.getUniqueString(),
        ]
        calls = [
            self.wrap(lambda v=value: v)
            for value in values
        ]
        results = list(async.gather(calls))

        self.assertItemsEqual(values, results)

    def test_returns_use_once_iterator(self):
        calls = []
        results = async.gather(calls)
        self.assertIsInstance(results, async.UseOnceIterator)

    def test_gather_from_calls_with_errors(self):
        calls = [
            (lambda: sentinel.okay),
            (lambda: 1 / 0),  # ZeroDivisionError
        ]
        calls = [self.wrap(call) for call in calls]
        results = list(async.gather(calls))

        self.assertThat(results, Contains(sentinel.okay))
        results.remove(sentinel.okay)
        self.assertThat(results, HasLength(1))
        failure = results[0]
        self.assertThat(failure, IsInstance(Failure))
        self.assertThat(failure.type, Is(ZeroDivisionError))


class TestUseOnceIterator(MAASTestCase):

    def test_returns_correct_items_for_list(self):
        expected_values = list(range(10))
        iterator = async.UseOnceIterator(expected_values)
        actual_values = [val for val in iterator]
        self.assertEqual(expected_values, actual_values)

    def test_raises_stop_iteration(self):
        iterator = async.UseOnceIterator([])
        self.assertRaises(StopIteration, iterator.next)

    def test_raises_iterator_reused(self):
        iterator = async.UseOnceIterator([])
        # Loop over the iterator to get to the point where we might try
        # and reuse it.
        list(iterator)
        self.assertRaises(IteratorReusedError, iterator.next)


class TestDeferredHooks(MAASTestCase, PostCommitHooksTestMixin):

    def test__is_thread_local(self):
        dhooks = DeferredHooks()
        queues = []
        for _ in xrange(3):
            thread = threading.Thread(
                target=lambda: queues.append(dhooks.hooks))
            thread.start()
            thread.join()
        self.assertThat(queues, HasLength(3))
        # Each queue is distinct (deque is unhashable; use the id() of each).
        self.assertThat(set(id(q) for q in queues), HasLength(3))

    def test__add_appends_Deferred_to_queue(self):
        dhooks = DeferredHooks()
        self.assertThat(dhooks.hooks, HasLength(0))
        dhooks.add(Deferred())
        self.assertThat(dhooks.hooks, HasLength(1))

    def test__add_cannot_be_called_in_the_reactor(self):
        dhooks = DeferredHooks()
        add_in_reactor = wait_for_reactor(dhooks.add)
        self.assertRaises(AssertionError, add_in_reactor, Deferred())

    def test__fire_calls_hooks(self):
        dhooks = DeferredHooks()
        ds = Deferred(), Deferred()
        for d in ds:
            dhooks.add(d)
        dhooks.fire()
        for d in ds:
            self.assertIsNone(extract_result(d))

    def test__fire_calls_hooks_in_reactor(self):

        def validate_in_reactor(_):
            self.assertTrue(isInIOThread())

        dhooks = DeferredHooks()
        d = Deferred()
        d.addCallback(validate_in_reactor)
        dhooks.add(d)
        dhooks.fire()
        self.assertThat(d, IsFiredDeferred())

    def test__fire_propagates_error_from_hook(self):
        error = factory.make_exception()
        dhooks = DeferredHooks()
        d = Deferred()
        d.addCallback(lambda _: Failure(error))
        dhooks.add(d)
        self.assertRaises(type(error), dhooks.fire)

    def test__fire_always_consumes_all_hooks(self):
        dhooks = DeferredHooks()
        d1, d2 = Deferred(), Deferred()
        d1.addCallback(lambda _: 0 / 0)  # d1 will fail.
        dhooks.add(d1)
        dhooks.add(d2)
        self.assertRaises(ZeroDivisionError, dhooks.fire)
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(d1, IsFiredDeferred())
        self.assertThat(d2, IsFiredDeferred())

    def test__reset_cancels_all_hooks(self):
        canceller = Mock()
        dhooks = DeferredHooks()
        d1, d2 = Deferred(canceller), Deferred(canceller)
        dhooks.add(d1)
        dhooks.add(d2)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(canceller, MockCallsMatch(call(d1), call(d2)))

    def test__reset_cancels_in_reactor(self):

        def validate_in_reactor(_):
            self.assertTrue(isInIOThread())

        dhooks = DeferredHooks()
        d = Deferred()
        d.addBoth(validate_in_reactor)
        dhooks.add(d)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(d, IsFiredDeferred())

    def test__reset_suppresses_CancelledError(self):
        logger = self.useFixture(TwistedLoggerFixture())

        dhooks = DeferredHooks()
        d = Deferred()
        dhooks.add(d)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(extract_result(d), Is(None))
        self.assertEqual("", logger.output)

    def test__logs_failures_from_cancellers(self):
        logger = self.useFixture(TwistedLoggerFixture())

        canceller = Mock()
        canceller.side_effect = factory.make_exception()

        dhooks = DeferredHooks()
        d = Deferred(canceller)
        dhooks.add(d)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        # The hook has not been fired, but because the user-supplied canceller
        # has failed we're not in a position to know what to do. This reflects
        # a programming error and not a run-time error that we ought to be
        # prepared for, so it is left as-is.
        self.assertThat(d, IsUnfiredDeferred())
        self.assertDocTestMatches(
            dedent("""\
            Failure when cancelling hook.
            Traceback (most recent call last):
            ...
            maastesting.factory.TestException#...
            """),
            logger.output)

    def test__logs_failures_from_cancellers_when_hook_already_fired(self):
        logger = self.useFixture(TwistedLoggerFixture())

        def canceller(d):
            d.callback(None)
            raise factory.make_exception()

        dhooks = DeferredHooks()
        d = Deferred(canceller)
        dhooks.add(d)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(d, IsFiredDeferred())
        self.assertDocTestMatches(
            dedent("""\
            Failure when cancelling hook.
            Traceback (most recent call last):
            ...
            maastesting.factory.TestException#...
            """),
            logger.output)

    def test__logs_failures_from_cancelled_hooks(self):
        logger = self.useFixture(TwistedLoggerFixture())

        error = factory.make_exception()
        dhooks = DeferredHooks()
        d = Deferred()
        d.addBoth(lambda _: Failure(error))
        dhooks.add(d)
        dhooks.reset()
        self.assertThat(dhooks.hooks, HasLength(0))
        self.assertThat(d, IsFiredDeferred())
        self.assertDocTestMatches(
            dedent("""\
            Unhandled Error
            Traceback (most recent call last):
            ...
            maastesting.factory.TestException#...
            """),
            logger.output)
