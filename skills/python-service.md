# Python network service: concurrency and hardening

Load before writing or reviewing a long-lived Python service. General guidance, not a solution
to any specific problem.

## 1. Pick one concurrency model

- Choose asyncio or threads for the whole service and say which. Mixing them is where bugs hide.
- asyncio: one task owns shared state, or shared state is only touched from the event loop thread.
  Never call blocking functions (`time.sleep`, blocking sockets, file I/O on big files) inside a
  coroutine; use `asyncio.to_thread` if unavoidable.
- Threads: one lock per piece of shared state, held briefly, never while doing I/O.

## 2. Connection lifecycle (asyncio)

- `asyncio.start_server` gives one handler task per connection. Keep a reference to every task you
  create, or it can be garbage collected mid-flight.
- Separate reading from writing when one slow peer could block others: a per-client
  `asyncio.Queue(maxsize=N)` drained by a writer task.
- Slow consumer policy is a decision: when a queue is full, drop the message or disconnect the
  client. Never `await queue.put()` from shared code, which would block everyone.
- On disconnect: cancel the writer task, close the writer, `await writer.wait_closed()`, and remove
  the client from shared state exactly once.

## 3. Timeouts and limits

- Wrap every read in `asyncio.wait_for(..., timeout)` or use `asyncio.timeout()`.
- `StreamReader.readline()` has a default 64 KiB limit; set `limit=` on `start_server` and handle
  `asyncio.LimitOverrunError` / `ValueError` by disconnecting.
- Cap concurrent connections with an `asyncio.Semaphore` checked at accept time.
- Rate-limit per client with a simple token bucket using `time.monotonic()`.

## 4. Untrusted input

- Decode bytes explicitly (`.decode("utf-8", errors="strict")`) and reject invalid input.
- Strip control characters, especially ESC (`\x1b`) and `\r`, before showing text to other users.
- Validate user-chosen identifiers with an allowlist regex; decide case sensitivity for uniqueness
  (`str.casefold()`); reserve names that could impersonate the system.

## 5. Errors and logging

- Never use a bare `except:`; catch specific exceptions and log them with context.
- `logging` with the connection id on every line; do not log message bodies by default.

## 6. Testing

- Start the server on `127.0.0.1` port 0 and read the real port from the server object.
- Use `asyncio.wait_for` around every read in tests so a broken test fails fast.
- Cover: two clients exchange data; a disconnect mid-broadcast; oversized input; shutdown leaves no
  pending tasks.

## 7. Agent suggestions to reject on sight

- Third-party frameworks for a plain TCP service.
- `time.sleep` in async code or in tests for synchronization.
- Unbounded `asyncio.create_task` per message per recipient.
- Shared dicts mutated from several threads without a lock.
- Bare `except:` or `except Exception: pass`.
