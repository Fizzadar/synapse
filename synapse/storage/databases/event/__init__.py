# Copyright 2014-2016 OpenMarket Ltd
# Copyright 2018 New Vector Ltd
# Copyright 2019-2021 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import TYPE_CHECKING

from synapse.config.homeserver import HomeServerConfig
from synapse.storage.database import (
    DatabasePool,
    LoggingDatabaseConnection,
)
from synapse.storage.engines import BaseDatabaseEngine
from synapse.storage.types import Cursor
from synapse.types import get_domain_from_id
from synapse.util.caches.stream_change_cache import StreamChangeCache

from .appservice import ApplicationServiceStore, ApplicationServiceTransactionStore
from .censor_events import CensorEventsStore
from .event_federation import EventFederationStore
from .event_push_actions import EventPushActionsStore
from .events_bg_updates import EventsBackgroundUpdatesStore
from .events_forward_extremities import EventForwardExtremitiesStore
from .purge_events import PurgeEventsStore
from .receipts import ReceiptsStore
from .rejections import RejectionsStore
from .relations import RelationsStore
from .room import RoomStore
from .room_batch import RoomBatchStore
from .roommember import RoomMemberStore
from .search import SearchStore
from .state import StateStore
from .stream import StreamWorkerStore
from .transactions import TransactionWorkerStore

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class EventDataStore(
    ApplicationServiceStore,
    ApplicationServiceTransactionStore,
    EventsBackgroundUpdatesStore,
    StateStore,
    PurgeEventsStore,
    EventFederationStore,
    EventPushActionsStore,
    CensorEventsStore,
    EventForwardExtremitiesStore,
    ReceiptsStore,
    RejectionsStore,
    RelationsStore,
    RoomStore,
    RoomBatchStore,
    RoomMemberStore,
    SearchStore,
    StreamWorkerStore,
    TransactionWorkerStore,
):
    def __init__(
        self,
        database: DatabasePool,
        db_conn: LoggingDatabaseConnection,
        hs: "HomeServer",
    ):
        self.hs = hs
        self._clock = hs.get_clock()
        self.database_engine = database.engine

        super().__init__(database, db_conn, hs)

        events_max = self._stream_id_gen.get_current_token()
        curr_state_delta_prefill, min_curr_state_delta_id = self.db_pool.get_cache_dict(
            db_conn,
            "current_state_delta_stream",
            entity_column="room_id",
            stream_column="stream_id",
            max_value=events_max,  # As we share the stream id with events token
            limit=1000,
        )
        self._curr_state_delta_stream_cache = StreamChangeCache(
            "_curr_state_delta_stream_cache",
            min_curr_state_delta_id,
            prefilled_cache=curr_state_delta_prefill,
        )

        self._stream_order_on_start = self.get_room_max_stream_ordering()
        self._min_stream_order_on_start = self.get_room_min_stream_ordering()

    def get_device_stream_token(self) -> int:
        return self._device_list_id_gen.get_current_token()


def check_database_before_upgrade(
    cur: Cursor, database_engine: BaseDatabaseEngine, config: HomeServerConfig
) -> None:
    """Called before upgrading an existing database to check that it is broadly sane
    compared with the configuration.
    """
    logger.info("Checking database for consistency with configuration...")

    # if there are any users in the database, check that the username matches our
    # configured server name.

    cur.execute("SELECT name FROM users LIMIT 1")
    rows = cur.fetchall()
    if not rows:
        return

    user_domain = get_domain_from_id(rows[0][0])
    if user_domain == config.server.server_name:
        return

    raise Exception(
        "Found users in database not native to %s!\n"
        "You cannot change a synapse server_name after it's been configured"
        % (config.server.server_name,)
    )


__all__ = ["EventDataStore", "check_database_before_upgrade"]
