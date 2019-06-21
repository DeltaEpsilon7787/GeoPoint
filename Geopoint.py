import asyncio
from collections import defaultdict
from functools import wraps
from json import dumps, loads
from random import choice
from smtplib import SMTP_SSL
from string import ascii_letters
from time import perf_counter, time

import pymongo
from attr import attrib, attrs
from attr import attrib, attrs
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.web import Application, RequestHandler
from tornado.websocket import WebSocketHandler
email_client = SMTP_SSL(host='smtp.gmail.com',
                        port=465)
email_client.login('scrapebot.test@gmail.com', 'alpha_beta')

API_METHODS = {}
def register_api(func):
    API_METHODS[func.__name__] = inner
    return func


async def check_login(username, password):
    return database_client.local.users.find_one({
        'username': username,
        'password': password
    })


async def user_in_db(username):
    return database_client.local.users.find_one({
        'username': username
    })


async def get_friend_list(username):
    return list(database_client.local.friendpairs.find({
        'username1': username
    })) + list(database_client.local.friendpairs.find({
        'username2': username
    }))


@attrs
class Activation(object):
    username: str = attrib(hash=False)
    key: str = attrib(hash=True)
    email: str = attrib(hash=False)
    time: float = attrib(hash=False)


class GeopointClient(WebSocketHandler):
    online_users: Dict[str, 'GeopointClient'] = {}

    outgoing_activations: Dict[str, Activation] = {}

    outgoing_friend_requests: Dict[str, List[str]] = defaultdict(list)
    notify_friend_req_response: Dict[str, List[str]] = defaultdict(list)

    async def open(self, username, password):
        if await self.check_login(username, password):
            GeopointClient.online_users[username] = self
            self.username = username
            self.generate_success(-1, code='AUTH_SUCCESS')
        else:
            self.generate_error(-1, code='NEED_AUTH')

    def require_auth(func):
        @wraps(func)
        def inner(self, id, *args, **kwargs):
            if self.username:
                func(self, **args, **kwargs)
            else:
                self.generate_error(id, 'NEED_AUTH')
        return inner

    def generate_success(self, id_, code='GENERIC_SUCCESS', data={}):
        self.write_message({
            'id': id_,
            'status': 'success',
            'code': code,
            'data': data
        })

    def generate_error(self, id_, code='GENERIC_ERROR', data={}):
        self.write_message({
            'id': id_,
            'status': 'fail',
            'code': code,
            'data': data
        })

    @classmethod
    def clear_old_activations(cls):
        for key, (_, time, _, _) in cls.outgoing_activations.items():
            if perf_counter() - time > 15 * 60:
                del cls.outgoing_activations[key]

    @register_api()
    async def get_time(self, id):
        self.generate_success(id, data=IOLoop.current().time())

    @register_api
    @require_auth
    async def geopoint_get(self, id):
        result = [
            {
                'lat': hit['lat'],
                'lon': hit['lon'],
                'time': hit['time']
            }
            for hit in database_client.local.points.find({'username': self.username})
        ]
        self.generate_success(id, data=result)

    @register_api
    @require_auth
    async def geopoint_get_friends(self, id):
        result = []

        for friend_datum in self.get_friend_list(self.username):
            friend_name = (
                friend_datum['username2']
                if friend_datum['username1'] == self.username
                else friend_datum['username1']
                if friend_datum['username1'] != self.username
                else ''
            )

            result.extend(
                {
                    'lat': hit['lat'],
                    'lon': hit['lon'],
                    'time': hit['time'],
                    'friend': friend_name
                }
                for hit in database_client.local.points.find({'username': friend_name})
            )
        self.generate_success(id, data=result)

    @register_api
    @require_auth
    async def geopoint_post(self, id, lat=None, lon=None):
        database_client.local.points.insert_one({
            'username': username,
            'time': time(),
            'lat': lat,
            'lon': lon
        })
        self.generate_success(id)

    @register_api
    @require_auth
    async def send_friend_request(self, id, target=None):
        if self.username == target:
            self.generate_error(id, 'FRIENDS_WITH_YOURSELF')
        elif not await self.user_in_db(target):
            self.generate_error(id, 'USER_DOES_NOT_EXIST', data=target)
        elif target in self.outgoing_friend_reqs[self.username]:
            self.generate_error(id, 'REPEAT_FRIEND_REQUEST', data=target)
        elif target in await self.get_my_friends():
            self.generate_error(id, 'ALREADY_FRIENDS', data=target)
        else:
            self.outgoing_friend_reqs[self.username].append(target)
            self.generate_success(id, data=target)

    @register_api
    @require_auth
    async def accept_friend_request(self, id, target=None):
        if not self.user_in_db(target):
            self.generate_error(id, 'USER_DOES_NOT_EXIST', data=target)
            return
        if self.username not in self.outgoing_friend_reqs[target]:
            self.generate_error(id, 'USER_NOT_SENT_FRIEND_REQUEST', data=target)
            return

        if is_accept:
            database_client.local.friendpairs.insert({
                'username1': target,
                'username2': self.username
            })
            self.generate_success(id, 'FRIEND_ADDED', data=target)
        else:
            self.generate_success(id, 'FRIEND_NOT_ADDED', data=target)
        self.notify_friend_req_response[target].append(
            (self.username, is_accept))
        self.outgoing_friend_reqs[target].remove(self.username)

    @register_api
    @require_auth
    async def get_my_friends(self, id):
        self.generate_success(id, data=[
            friend_datum['username2']
            if friend_datum['username1'] == self.username
            else friend_datum['username1']
            for friend_datum in await self.get_friend_list(self.username)
        ])

    @register_api
    async def register(self, id, username=None, password=None, email=None):
        self.clear_old_activations()

        if email in self.outgoing_activations:
            self.generate_error(id, 'ACTIVATION_IN_PROGRESS')
        elif self.user_in_db(username):
            self.generate_error(id, 'USER_ALREADY_EXISTS', data=username)
        else:
            generated_key = ''.join(choice(digits) for _ in range(6))
            self.outgoing_activations[generated_key] = (
                email, perf_counter(), username, password)

            email_client.sendmail(
                'Geopoint Bot',
                [email],
                (
                    "From: Geopoint Bot\n"
                    f"To: {email}\n"
                    "Subject: Activation\n"
                    "\n"
                    "Somebody has used this email to register at GeoPoint app. "
                    "If this doesn't look familiar, ignore this email.\n"
                    f"Enter this key to accept: {generated_key}"
                )
            )
            self.generate_success(id)

    @register_api
    async def activate(self, id, key=None):
        self.clear_old_activations()

        if key not in self.outgoing_activations:
            self.generate_error(id, 'INVALID_KEY')
        else:
            email, _, username, password = GeopointClient.outgoing_activations[key]
            database_client.local.users.insert_one({
                'username': username,
                'password': password,
                'email': email
            })
            self.generate_success(id)
            del GeopointClient.outgoing_activations[key]

    @register_api
    @require_auth
    async def get_friend_requests(self, id):
        wannabe_friends = [
            wannabe_friend
            for wannabe_friend, requests in self.outgoing_friend_reqs.items()
            if username in requests
        ]

        self.generate_success(id, data=wannabe_friends)

    async def on_message(self, message):
        self.current_user()
        print(message)
        try:
            data = loads(message)
        except Exception:
            self.generate_error(-1, 'JSON_DECODE_ERROR')
            return

        try:
            action = data['action']
        except Exception:
            self.generate_error(-1, 'ACTION_NOT_DEFINED')
            return

        try:
            id = data['id']
        except KeyError:
            self.generate_error(-1, 'ID_NOT_SPECIFIED')
            return

        if action not in API_METHODS:
            self.generate_error(id, 'UNKNOWN_ACTION')
            return

        del data['action']
        del data['id']

        try:
            yield API_METHODS[action](self, id, **data)
        except Exception as E:
            print(E)
            self.generate_error(id, )


app = Application(
    [
        ('/websocket/([a-zA-Z0-9_]+)/([a-f0-9]{64})', GeopointClient)
    ],
    websocket_ping_interval=5,
    websocket_ping_timeout=300
)

app.listen(8010)

print('The server is up')
print(API_METHODS)

PeriodicCallback(GeopointClient.send_friend_requests, 5000)
IOLoop.current().start()
