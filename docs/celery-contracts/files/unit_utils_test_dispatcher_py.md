### `t/unit/utils/test_dispatcher.py`

Celery's internal signal dispatcher (hooks fired on events such as job start/finish). Portable guarantee for sqlery: lifecycle hooks are delivered reliably, one failing hook never breaks the job or other hooks, and registration/removal leaves no leftovers.

**Delivery and filtering**
- Register a hook for one specific sender, fire an event from that sender; expect: hook called once, its return value collected.
- Register a hook with no sender filter, fire from any sender; expect: hook called.
- Register a hook, fire an event, then unregister it; expect: no hooks left registered (clean state).

**Failing hooks**
- Register a hook that raises, fire an event; expect: the sender is not crashed, and after unregistering nothing is left behind.
- Register a hook that fails on its first 2 calls with retry enabled, fire once; expect: hook retried until success, its final value returned.
- Register a retry-enabled hook with a custom unique id; expect: it is stored under that id and can be unregistered by it.
- Register a retry-enabled hook through the decorator form, fire, then unregister it; expect: unregister works and nothing is left (regression for issue 9119).

**Duplicates and unique ids**
- Register the same hook 6 times; expect: exactly 1 registration, fires once.
- Register the same bound method of one object twice; expect: only 1 registration.
- Register two different hooks under the same unique id; expect: the second replaces the first, 1 registration.

**Multiple objects**
- Register the same method on 2 different objects; expect: 2 registrations, both fire in registration order.
- Unregister one object's method (returns true); expect: the other object's hook still fires, only its result returned.
- Two retry-enabled methods on different objects, fire; expect: both return values; unregistering one leaves exactly 1.

**Cleanup and lifetime**
- Register a bound method, drop the last reference to its object, fire; expect: no call, no error, registry empty (dead hooks are dropped automatically).
- Register 3 hooks, unregister one, drop another's object; expect: remaining hook unregisters cleanly and registry is empty.
- Register a bound method and keep the object alive across garbage collection; expect: still fires; after dropping it, registry empties.
- Drop one of two objects' hooks; expect: the surviving object's hook still fires.
