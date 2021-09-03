# Copyright 2018 New Vector
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
from unittest.mock import Mock

from twisted.internet import defer

import synapse.rest.admin
from synapse.api.constants import (
    EventTypes,
    Membership,
    RoomEncryptionAlgorithms,
    UserTypes,
)
from synapse.api.room_versions import RoomVersion, RoomVersions
from synapse.rest.client import login, room, user_directory
from synapse.storage.roommember import ProfileInfo
from synapse.types import create_requester

from tests import unittest
from tests.storage.test_user_directory import GetUserDirectoryTables
from tests.unittest import HomeserverTestCase, override_config


class UserDirectoryTestCase(GetUserDirectoryTables, HomeserverTestCase):
    """
    Tests the UserDirectoryHandler.

    We're broadly testing two kinds of things here.

    1. Check that we correctly update the user directory in response
       to events (e.g. join a room, leave a room, change name, make public)
    2. Check that the search logic behaves as expected.
    """

    servlets = [
        login.register_servlets,
        synapse.rest.admin.register_servlets_for_client_rest_resource,
        room.register_servlets,
    ]

    def make_homeserver(self, reactor, clock):

        config = self.default_config()
        config["update_user_directory"] = True
        return self.setup_test_homeserver(config=config)

    def prepare(self, reactor, clock, hs):
        self.store = hs.get_datastore()
        self.handler = hs.get_user_directory_handler()
        self.event_builder_factory = self.hs.get_event_builder_factory()
        self.event_creation_handler = self.hs.get_event_creation_handler()

    def test_handle_local_profile_change_with_support_user(self):
        support_user_id = "@support:test"
        self.get_success(
            self.store.register_user(
                user_id=support_user_id, password_hash=None, user_type=UserTypes.SUPPORT
            )
        )
        regular_user_id = "@regular:test"
        self.get_success(
            self.store.register_user(user_id=regular_user_id, password_hash=None)
        )

        self.get_success(
            self.handler.handle_local_profile_change(support_user_id, None)
        )
        profile = self.get_success(self.store.get_user_in_directory(support_user_id))
        self.assertTrue(profile is None)
        display_name = "display_name"

        profile_info = ProfileInfo(avatar_url="avatar_url", display_name=display_name)
        self.get_success(
            self.handler.handle_local_profile_change(regular_user_id, profile_info)
        )
        profile = self.get_success(self.store.get_user_in_directory(regular_user_id))
        self.assertTrue(profile["display_name"] == display_name)

    def test_handle_local_profile_change_with_deactivated_user(self):
        # create user
        r_user_id = "@regular:test"
        self.get_success(
            self.store.register_user(user_id=r_user_id, password_hash=None)
        )

        # update profile
        display_name = "Regular User"
        profile_info = ProfileInfo(avatar_url="avatar_url", display_name=display_name)
        self.get_success(
            self.handler.handle_local_profile_change(r_user_id, profile_info)
        )

        # profile is in directory
        profile = self.get_success(self.store.get_user_in_directory(r_user_id))
        self.assertTrue(profile["display_name"] == display_name)

        # deactivate user
        self.get_success(self.store.set_user_deactivated_status(r_user_id, True))
        self.get_success(self.handler.handle_local_user_deactivated(r_user_id))

        # profile is not in directory
        profile = self.get_success(self.store.get_user_in_directory(r_user_id))
        self.assertTrue(profile is None)

        # update profile after deactivation
        self.get_success(
            self.handler.handle_local_profile_change(r_user_id, profile_info)
        )

        # profile is furthermore not in directory
        profile = self.get_success(self.store.get_user_in_directory(r_user_id))
        self.assertTrue(profile is None)

    def test_handle_user_deactivated_support_user(self):
        s_user_id = "@support:test"
        self.get_success(
            self.store.register_user(
                user_id=s_user_id, password_hash=None, user_type=UserTypes.SUPPORT
            )
        )

        self.store.remove_from_user_dir = Mock(return_value=defer.succeed(None))
        self.get_success(self.handler.handle_local_user_deactivated(s_user_id))
        self.store.remove_from_user_dir.not_called()

    def test_handle_user_deactivated_regular_user(self):
        r_user_id = "@regular:test"
        self.get_success(
            self.store.register_user(user_id=r_user_id, password_hash=None)
        )
        self.store.remove_from_user_dir = Mock(return_value=defer.succeed(None))
        self.get_success(self.handler.handle_local_user_deactivated(r_user_id))
        self.store.remove_from_user_dir.called_once_with(r_user_id)

    def test_reactivation_makes_regular_user_searchable(self):
        user = self.register_user("regular", "pass")
        user_token = self.login(user, "pass")
        admin_user = self.register_user("admin", "pass", admin=True)

        # Ensure the regular user is publicly visible and searchable.
        public_room = self.helper.create_room_as(user, is_public=True, tok=user_token)
        s = self.get_success(self.handler.search_users(admin_user, user, 10))
        self.assertEqual(len(s["results"]), 1)
        self.assertEqual(s["results"][0]["user_id"], user)

        # Deactivate the user and check they're not searchable.
        deactivate_handler = self.hs._deactivate_account_handler
        self.get_success(
            deactivate_handler.deactivate_account(
                user, erase_data=False, requester=create_requester(admin_user)
            )
        )
        s = self.get_success(self.handler.search_users(admin_user, user, 10))
        self.assertEqual(s["results"], [])

        # Reactivate the user and make them publicly visible again.
        self.get_success(deactivate_handler.activate_account(user))
        self.inject_room_member(public_room, user, Membership.JOIN)

        # Check they're searchable.
        s = self.get_success(self.handler.search_users(admin_user, user, 10))
        self.assertEqual(len(s["results"]), 1)
        self.assertEqual(s["results"][0]["user_id"], user)

    def test_private_room(self):
        """
        A user can be searched for only by people that are either in a public
        room, or that share a private chat.
        """
        u1 = self.register_user("user1", "pass")
        u1_token = self.login(u1, "pass")
        u2 = self.register_user("user2", "pass")
        u2_token = self.login(u2, "pass")
        u3 = self.register_user("user3", "pass")

        # We do not add users to the directory until they join a room.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

        room = self.helper.create_room_as(u1, is_public=False, tok=u1_token)
        self.helper.invite(room, src=u1, targ=u2, tok=u1_token)
        self.helper.join(room, user=u2, tok=u2_token)

        # Check we have populated the database correctly.
        shares_private = self.get_users_who_share_private_rooms()
        public_users = self.get_users_in_public_rooms()

        self.assertEqual(
            self._compress_shared(shares_private), {(u1, u2, room), (u2, u1, room)}
        )
        self.assertEqual(public_users, [])

        # We get one search result when searching for user2 by user1.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 1)

        # We get NO search results when searching for user2 by user3.
        s = self.get_success(self.handler.search_users(u3, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

        # We get NO search results when searching for user3 by user1.
        s = self.get_success(self.handler.search_users(u1, "user3", 10))
        self.assertEqual(len(s["results"]), 0)

        # User 2 then leaves.
        self.helper.leave(room, user=u2, tok=u2_token)

        # Check we have removed the values.
        shares_private = self.get_users_who_share_private_rooms()
        public_users = self.get_users_in_public_rooms()

        self.assertEqual(self._compress_shared(shares_private), set())
        self.assertEqual(public_users, [])

        # User1 now gets no search results for any of the other users.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

        s = self.get_success(self.handler.search_users(u1, "user3", 10))
        self.assertEqual(len(s["results"]), 0)

    @override_config({"encryption_enabled_by_default_for_room_type": "all"})
    def test_encrypted_by_default_config_option_all(self):
        """Tests that invite-only and non-invite-only rooms have encryption enabled by
        default when the config option encryption_enabled_by_default_for_room_type is "all".
        """
        # Create a user
        user = self.register_user("user", "pass")
        user_token = self.login(user, "pass")

        # Create an invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=False, tok=user_token)

        # Check that the room has an encryption state event
        event_content = self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
        )
        self.assertEqual(event_content, {"algorithm": RoomEncryptionAlgorithms.DEFAULT})

        # Create a non invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=True, tok=user_token)

        # Check that the room has an encryption state event
        event_content = self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
        )
        self.assertEqual(event_content, {"algorithm": RoomEncryptionAlgorithms.DEFAULT})

    @override_config({"encryption_enabled_by_default_for_room_type": "invite"})
    def test_encrypted_by_default_config_option_invite(self):
        """Tests that only new, invite-only rooms have encryption enabled by default when
        the config option encryption_enabled_by_default_for_room_type is "invite".
        """
        # Create a user
        user = self.register_user("user", "pass")
        user_token = self.login(user, "pass")

        # Create an invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=False, tok=user_token)

        # Check that the room has an encryption state event
        event_content = self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
        )
        self.assertEqual(event_content, {"algorithm": RoomEncryptionAlgorithms.DEFAULT})

        # Create a non invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=True, tok=user_token)

        # Check that the room does not have an encryption state event
        self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
            expect_code=404,
        )

    @override_config({"encryption_enabled_by_default_for_room_type": "off"})
    def test_encrypted_by_default_config_option_off(self):
        """Tests that neither new invite-only nor non-invite-only rooms have encryption
        enabled by default when the config option
        encryption_enabled_by_default_for_room_type is "off".
        """
        # Create a user
        user = self.register_user("user", "pass")
        user_token = self.login(user, "pass")

        # Create an invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=False, tok=user_token)

        # Check that the room does not have an encryption state event
        self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
            expect_code=404,
        )

        # Create a non invite-only room as that user
        room_id = self.helper.create_room_as(user, is_public=True, tok=user_token)

        # Check that the room does not have an encryption state event
        self.helper.get_state(
            room_id=room_id,
            event_type=EventTypes.RoomEncryption,
            tok=user_token,
            expect_code=404,
        )

    def test_spam_checker(self):
        """
        A user which fails the spam checks will not appear in search results.
        """
        u1 = self.register_user("user1", "pass")
        u1_token = self.login(u1, "pass")
        u2 = self.register_user("user2", "pass")
        u2_token = self.login(u2, "pass")

        # We do not add users to the directory until they join a room.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

        room = self.helper.create_room_as(u1, is_public=False, tok=u1_token)
        self.helper.invite(room, src=u1, targ=u2, tok=u1_token)
        self.helper.join(room, user=u2, tok=u2_token)

        # Check we have populated the database correctly.
        shares_private = self.get_users_who_share_private_rooms()
        public_users = self.get_users_in_public_rooms()

        self.assertEqual(
            self._compress_shared(shares_private), {(u1, u2, room), (u2, u1, room)}
        )
        self.assertEqual(public_users, [])

        # We get one search result when searching for user2 by user1.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 1)

        async def allow_all(user_profile):
            # Allow all users.
            return False

        # Configure a spam checker that does not filter any users.
        spam_checker = self.hs.get_spam_checker()
        spam_checker._check_username_for_spam_callbacks = [allow_all]

        # The results do not change:
        # We get one search result when searching for user2 by user1.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 1)

        # Configure a spam checker that filters all users.
        async def block_all(user_profile):
            # All users are spammy.
            return True

        spam_checker._check_username_for_spam_callbacks = [block_all]

        # User1 now gets no search results for any of the other users.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

    def test_legacy_spam_checker(self):
        """
        A spam checker without the expected method should be ignored.
        """
        u1 = self.register_user("user1", "pass")
        u1_token = self.login(u1, "pass")
        u2 = self.register_user("user2", "pass")
        u2_token = self.login(u2, "pass")

        # We do not add users to the directory until they join a room.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 0)

        room = self.helper.create_room_as(u1, is_public=False, tok=u1_token)
        self.helper.invite(room, src=u1, targ=u2, tok=u1_token)
        self.helper.join(room, user=u2, tok=u2_token)

        # Check we have populated the database correctly.
        shares_private = self.get_users_who_share_private_rooms()
        public_users = self.get_users_in_public_rooms()

        self.assertEqual(
            self._compress_shared(shares_private), {(u1, u2, room), (u2, u1, room)}
        )
        self.assertEqual(public_users, [])

        # Configure a spam checker.
        spam_checker = self.hs.get_spam_checker()
        # The spam checker doesn't need any methods, so create a bare object.
        spam_checker.spam_checker = object()

        # We get one search result when searching for user2 by user1.
        s = self.get_success(self.handler.search_users(u1, "user2", 10))
        self.assertEqual(len(s["results"]), 1)

    def test_search_all_users(self):
        """
        Search all users = True means that a user does not have to share a
        private room with the searching user or be in a public room to be search
        visible.
        """
        self.handler.search_all_users = True
        self.hs.config.user_directory_search_all_users = True

        u1 = self.register_user("user1", "pass")
        self.register_user("user2", "pass")
        u3 = self.register_user("user3", "pass")

        shares_private = self.get_users_who_share_private_rooms()
        public_users = self.get_users_in_public_rooms()

        # No users share rooms
        self.assertEqual(public_users, [])
        self.assertEqual(self._compress_shared(shares_private), set())

        # Despite not sharing a room, search_all_users means we get a search
        # result.
        s = self.get_success(self.handler.search_users(u1, u3, 10))
        self.assertEqual(len(s["results"]), 1)

        # We can find the other two users
        s = self.get_success(self.handler.search_users(u1, "user", 10))
        self.assertEqual(len(s["results"]), 2)

        # Registering a user and then searching for them works.
        u4 = self.register_user("user4", "pass")
        s = self.get_success(self.handler.search_users(u1, u4, 10))
        self.assertEqual(len(s["results"]), 1)

    @override_config(
        {
            "user_directory": {
                "enabled": True,
                "search_all_users": True,
                "prefer_local_users": True,
            }
        }
    )
    def test_prefer_local_users(self):
        """Tests that local users are shown higher in search results when
        user_directory.prefer_local_users is True.
        """
        # Create a room and few users to test the directory with
        searching_user = self.register_user("searcher", "password")
        searching_user_tok = self.login("searcher", "password")

        room_id = self.helper.create_room_as(
            searching_user,
            room_version=RoomVersions.V1.identifier,
            tok=searching_user_tok,
        )

        # Create a few local users and join them to the room
        local_user_1 = self.register_user("user_xxxxx", "password")
        local_user_2 = self.register_user("user_bbbbb", "password")
        local_user_3 = self.register_user("user_zzzzz", "password")

        self._add_user_to_room(room_id, RoomVersions.V1, local_user_1)
        self._add_user_to_room(room_id, RoomVersions.V1, local_user_2)
        self._add_user_to_room(room_id, RoomVersions.V1, local_user_3)

        # Create a few "remote" users and join them to the room
        remote_user_1 = "@user_aaaaa:remote_server"
        remote_user_2 = "@user_yyyyy:remote_server"
        remote_user_3 = "@user_ccccc:remote_server"
        self._add_user_to_room(room_id, RoomVersions.V1, remote_user_1)
        self._add_user_to_room(room_id, RoomVersions.V1, remote_user_2)
        self._add_user_to_room(room_id, RoomVersions.V1, remote_user_3)

        local_users = [local_user_1, local_user_2, local_user_3]
        remote_users = [remote_user_1, remote_user_2, remote_user_3]

        # The local searching user searches for the term "user", which other users have
        # in their user id
        results = self.get_success(
            self.handler.search_users(searching_user, "user", 20)
        )["results"]
        received_user_id_ordering = [result["user_id"] for result in results]

        # Typically we'd expect Synapse to return users in lexicographical order,
        # assuming they have similar User IDs/display names, and profile information.

        # Check that the order of returned results using our module is as we expect,
        # i.e our local users show up first, despite all users having lexographically mixed
        # user IDs.
        [self.assertIn(user, local_users) for user in received_user_id_ordering[:3]]
        [self.assertIn(user, remote_users) for user in received_user_id_ordering[3:]]

    def _add_user_to_room(
        self,
        room_id: str,
        room_version: RoomVersion,
        user_id: str,
    ):
        # Add a user to the room.
        builder = self.event_builder_factory.for_room_version(
            room_version,
            {
                "type": "m.room.member",
                "sender": user_id,
                "state_key": user_id,
                "room_id": room_id,
                "content": {"membership": "join"},
            },
        )

        event, context = self.get_success(
            self.event_creation_handler.create_new_client_event(builder)
        )

        self.get_success(
            self.hs.get_storage().persistence.persist_event(event, context)
        )

    def test_making_room_public_doesnt_alter_directory_entry(self):
        """Per-room names shouldn't go to the directory when the room becomes public.

        I made this a Synapse test case rather than a Complement one because
        I think this is (strictly speaking) an implementation choice. Synapse
        has chosen to only ever use the public profile when responding to a user
        directory search. There's no privacy leak here, because making the room
        public discloses the per-room name.

        The spec doesn't mandate anything about _how_ a user
        should appear in a /user_directory/search result. Hypothetical example:
        suppose Bob searches for Alice. When representing Alice in a search
        result, it's reasonable to use any of Alice's nicknames that Bob is
        aware of. Heck, maybe we even want to use lots of them in a combined
        displayname like `Alice (aka "ali", "ally", "41iC3")`.
        """
        # TODO the same should apply when Alice is a remote user.
        alice = self.register_user("alice", "pass")
        alice_token = self.login(alice, "pass")
        bob = self.register_user("bob", "pass")
        bob_token = self.login(bob, "pass")

        # Alice and Bob are in a private room.
        room = self.helper.create_room_as(alice, is_public=False, tok=alice_token)
        self.helper.invite(room, src=alice, targ=bob, tok=alice_token)
        self.helper.join(room, user=bob, tok=bob_token)

        # Alice has a nickname unique to that room.
        self.helper.send_state(
            room,
            "m.room.member",
            {
                "displayname": "Freddy Mercury",
                "membership": "join",
            },
            alice_token,
            state_key=alice,
        )

        # Check Alice isn't recorded as being in a public room.
        self.assertNotIn((alice, room), self.get_users_in_public_rooms())

        # One of them makes the room public.
        self.helper.send_state(
            room,
            "m.room.join_rules",
            {"join_rule": "public"},
            alice_token,
        )
        # Check that Alice is now recorded as being in a public room
        self.assertIn((alice, room), self.get_users_in_public_rooms())

        # Alice's display name remains the same in the user directory.
        search_result = self.get_success(self.handler.search_users(bob, alice, 10))
        self.assertEqual(
            search_result["results"],
            [{"display_name": "alice", "avatar_url": None, "user_id": alice}],
            0,
        )


class TestUserDirSearchDisabled(unittest.HomeserverTestCase):
    user_id = "@test:test"

    servlets = [
        user_directory.register_servlets,
        room.register_servlets,
        login.register_servlets,
        synapse.rest.admin.register_servlets_for_client_rest_resource,
    ]

    def make_homeserver(self, reactor, clock):
        config = self.default_config()
        config["update_user_directory"] = True
        hs = self.setup_test_homeserver(config=config)

        self.config = hs.config

        return hs

    def test_disabling_room_list(self):
        self.config.user_directory_search_enabled = True

        # First we create a room with another user so that user dir is non-empty
        # for our user
        self.helper.create_room_as(self.user_id)
        u2 = self.register_user("user2", "pass")
        room = self.helper.create_room_as(self.user_id)
        self.helper.join(room, user=u2)

        # Assert user directory is not empty
        channel = self.make_request(
            "POST", b"user_directory/search", b'{"search_term":"user2"}'
        )
        self.assertEquals(200, channel.code, channel.result)
        self.assertTrue(len(channel.json_body["results"]) > 0)

        # Disable user directory and check search returns nothing
        self.config.user_directory_search_enabled = False
        channel = self.make_request(
            "POST", b"user_directory/search", b'{"search_term":"user2"}'
        )
        self.assertEquals(200, channel.code, channel.result)
        self.assertTrue(len(channel.json_body["results"]) == 0)
