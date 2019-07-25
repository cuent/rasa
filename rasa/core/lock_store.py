import asyncio
import json
import logging
from typing import Text, Optional, Dict, Union

from async_generator import asynccontextmanager, async_generator, yield_

from rasa.core.constants import DEFAULT_LOCK_LIFETIME
from rasa.core.lock import TicketLock
from rasa.utils.endpoints import EndpointConfig

logger = logging.getLogger(__name__)

ACCEPTED_LOCK_STORES = ["in_memory", "redis"]


# noinspection PyUnresolvedReferences
class LockError(Exception):
    """Exception that is raised when a lock cannot be acquired.

     Attributes:
          message (str): explanation of which `conversation_id` raised the error
    """

    pass


class LockStore(object):
    def __init__(self, lifetime: int = DEFAULT_LOCK_LIFETIME) -> None:
        self.lifetime = lifetime

    @staticmethod
    def find_lock_store(store: EndpointConfig = None) -> "LockStore":
        if store is None or store.type is None or store.type == "in_memory":
            lock_store = InMemoryLockStore()
        elif store.type == "redis":
            lock_store = RedisLockStore(host=store.url, **store.kwargs)
        else:
            raise ValueError(
                "Cannot create `LockStore` of type '{}'. One of the following "
                "`LockStore` types need to be specified: {}."
                "".format(store.type, ", ".join(ACCEPTED_LOCK_STORES))
            )

        logger.debug(
            "Connected to lock store {}.".format(lock_store.__class__.__name__)
        )

        return lock_store

    def create_lock(self, conversation_id: Text) -> TicketLock:
        """Create and save a new `TicketLock` for `conversation_id`."""

        lock = TicketLock(conversation_id)
        self.save_lock(lock)
        return lock

    def get_lock(self, conversation_id: Text) -> Optional[TicketLock]:
        """Fetch lock for `conversation_id` from storage."""

        raise NotImplementedError

    def delete_lock(self, conversation_id: Text) -> None:
        """Delete lock for `conversation_id` from storage."""

        raise NotImplementedError

    def save_lock(self, lock: TicketLock) -> None:
        """Commit `lock` to storage."""

        raise NotImplementedError

    def issue_ticket(
        self, conversation_id: Text, lifetime: Union[float, int] = DEFAULT_LOCK_LIFETIME
    ) -> int:
        """Issue new ticket for lock associated with `conversation_id`.

        Creates a new lock if none is found.
        """

        lock = self.get_or_create_lock(conversation_id)

        lifetime = lifetime if lifetime is not None else self.lifetime
        ticket = lock.issue_ticket(lifetime)

        self.save_lock(lock)
        return ticket

    @asynccontextmanager
    @async_generator
    async def lock(
        self,
        conversation_id: Text,
        attempts: int = DEFAULT_LOCK_LIFETIME,
        wait_time_in_seconds: Union[int, float] = 1,
    ) -> None:
        """Acquire lock for `conversation_id` with `ticket`.

        Perform `attempts` with a wait_time_in_seconds of `wait_time_in_seconds` seconds
         between them before raising a `LockError`.
        """

        ticket = self.issue_ticket(conversation_id)

        try:
            # have to use async_generator.yield_() for py 3.5 compatibility
            await yield_(
                await self._acquire_lock(
                    conversation_id, ticket, attempts, wait_time_in_seconds
                )
            )
        finally:
            self.cleanup(conversation_id, ticket)

    async def _acquire_lock(
        self, conversation_id: Text, ticket: int, attempts: int, wait: Union[int, float]
    ) -> TicketLock:

        while attempts > 0:
            # fetch lock in every iteration because lock might no longer exist
            lock = self.get_lock(conversation_id)

            # exit loop if lock does not exist anymore (expired)
            if not lock:
                break

            # acquire lock if it isn't locked
            if not lock.is_locked(ticket):
                return lock

            # sleep and update lock
            await asyncio.sleep(wait)
            self.update_lock(conversation_id)

            attempts -= 1

        raise LockError(
            "Could not acquire lock for conversation_id '{}'."
            "".format(conversation_id)
        )

    def update_lock(self, conversation_id: Text) -> None:
        """Fetch lock for `conversation_id`, remove expired tickets and save lock."""

        lock = self.get_lock(conversation_id)
        if lock:
            lock.remove_expired_tickets()
            self.save_lock(lock)

    def get_or_create_lock(self, conversation_id: Text) -> TicketLock:
        """Fetch existing lock for `conversation_id` or create a new one if
        it doesn't exist."""

        existing_lock = self.get_lock(conversation_id)

        if existing_lock:
            return existing_lock

        return self.create_lock(conversation_id)

    def is_someone_waiting(self, conversation_id: Text) -> bool:
        """Return whether someone is waiting for lock associated with
        `conversation_id`."""

        lock = self.get_lock(conversation_id)
        if lock:
            return lock.is_someone_waiting()

        return False

    def finish_serving(self, conversation_id: Text, ticket_number: int) -> None:
        """Finish serving ticket with `ticket_number` for `conversation_id`.

        Removes ticket from lock and saves lock.
        """

        lock = self.get_lock(conversation_id)
        if lock:
            lock.remove_ticket_for(ticket_number)
            self.save_lock(lock)

    def cleanup(self, conversation_id: Text, ticket_number: int) -> None:
        """Remove lock for `conversation_id` if no one is waiting."""
        self.finish_serving(conversation_id, ticket_number)
        if not self.is_someone_waiting(conversation_id):
            self.delete_lock(conversation_id)

    @staticmethod
    def _log_deletion(conversation_id: Text, deletion_successful: bool) -> None:
        if deletion_successful:
            logger.debug("Deleted lock for conversation '{}'.".format(conversation_id))
        else:
            logger.debug(
                "Could not delete lock for conversation '{}'.".format(conversation_id)
            )


class RedisLockStore(LockStore):
    """Redis store for ticket locks."""

    def __init__(
        self,
        host: Text = "localhost",
        port: int = 6379,
        db: int = 1,
        password: Optional[Text] = None,
        lifetime: int = DEFAULT_LOCK_LIFETIME,
    ):
        import redis

        self.red = redis.StrictRedis(
            host=host, port=int(port), db=int(db), password=password
        )
        super().__init__(lifetime)

    def get_lock(self, conversation_id: Text) -> Optional[TicketLock]:
        serialised_lock = self.red.get(conversation_id)
        if serialised_lock:
            return TicketLock.from_dict(json.loads(serialised_lock))

    def delete_lock(self, conversation_id: Text) -> None:
        deletion_successful = self.red.delete(conversation_id)
        self._log_deletion(conversation_id, deletion_successful)

    def save_lock(self, lock: TicketLock) -> None:
        self.red.set(lock.conversation_id, lock.dumps())


class InMemoryLockStore(LockStore):
    """In-memory store for ticket locks."""

    def __init__(self, lifetime: int = DEFAULT_LOCK_LIFETIME):
        self.conversation_locks = {}  # type: Dict[Text, TicketLock]
        super().__init__(lifetime)

    def get_lock(self, conversation_id: Text) -> Optional[TicketLock]:
        return self.conversation_locks.get(conversation_id)

    def delete_lock(self, conversation_id: Text) -> None:
        deleted_lock = self.conversation_locks.pop(conversation_id, None)
        self._log_deletion(
            conversation_id, deletion_successful=deleted_lock is not None
        )

    def save_lock(self, lock: TicketLock) -> None:
        self.conversation_locks[lock.conversation_id] = lock
