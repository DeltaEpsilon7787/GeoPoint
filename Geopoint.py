import logging
from collections import defaultdict
from functools import wraps
from json import loads
from operator import itemgetter
from os import getcwd, mkdir
from os.path import join as path_join
from random import choice
from smtplib import SMTP_SSL
from string import digits
from time import perf_counter
from typing import Any, Dict, List

import pymongo
from tornado.ioloop import IOLoop
from tornado.web import Application, StaticFileHandler
from tornado.websocket import WebSocketHandler

from math import radians, cos

database_client = pymongo.MongoClient(host='localhost', port=27017)
API_METHODS = {}


def register_api(func):
    API_METHODS[func.__name__] = func
    return func


class EMailSender(object):
    _email_client = None

    @classmethod
    def _login(cls):
        cls._email_client = SMTP_SSL(host='smtp.gmail.com', port=465)
        cls._email_client.login('scrapebot.test@gmail.com', 'alpha_beta')

    @classmethod
    def send_mail(cls, *args):
        if cls._email_client is None:
            cls._login()

        try:
            cls._email_client.sendmail(*args)
        except:
            cls._login()
            try:
                cls._email_client.send_mail(*args)
            except:
                raise


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
    return [
        pair['username1'] == username and pair['username2'] or pair['username1']
        for pair in database_client.local.friendpairs.find({
            '$or': [
                {'username1': username},
                {'username2': username},
            ]
        })
    ]


def require_auth(func):
    @wraps(func)
    async def inner(self, id_, *args, **kwargs):
        if self.username:
            await func(self, id_, *args, **kwargs)
        else:
            self.generate_error(-1, 'NEED_AUTH')

    return inner


def approximate_distance(lat1, lon1, lat2, lon2):
    R = 6371008.7714150598

    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)

    return R * (
        delta_phi**2 +
        (
            delta_lambda *
            cos(
                radians(
                    0.5 *
                    (lat1 + lat2)
                )
            )
        )**2
    )**0.5


class Activation(object):
    def __init__(self, username, password, email):
        self.username = username
        self.password = password
        self.email = email

        self.time = perf_counter()

    def __hash__(self):
        return hash(self.username)


class GeopointClient(WebSocketHandler):
    online_users: Dict[str, List['GeopointClient']] = defaultdict(list)

    outgoing_activations: Dict[str, Activation] = {}

    outgoing_friend_requests: Dict[str, List[str]] = defaultdict(list)
    inbound_friend_requests: Dict[str, List[str]] = defaultdict(list)

    def initialize(self, guest_session=False):
        self.username = None
        self.guest_session = guest_session

    def check_origin(self, origin):
        return True

    async def open(self, username=None, password=None):
        if not self.guest_session:
            if await check_login(username, password):
                GeopointClient.online_users[username].append(self)
                self.username = username
                self.write_message('AUTH_SUCCESSFUL')
            else:
                self.write_message('AUTH_FAILED')
        else:
            self.write_message('GUEST_SESSION')

    async def call_api(self, func, id_, **data):
        try:
            await func(self, id_, **data)
        except RuntimeError as E:
            self.generate_error(-1, 'INTERNAL_ERROR')
            print(E)

    def on_message(self, message):
        print(message)
        try:
            data = loads(message)
        except RuntimeError:
            self.close(1003)
            return

        try:
            action = data['action']
        except RuntimeError:
            self.close(1003)
            return

        try:
            id_ = data['id']
        except KeyError:
            self.generate_error(-1, 'ID_NOT_SPECIFIED')
            return

        if action not in API_METHODS:
            self.close(1008)
            return

        del data['action']
        del data['id']

        IOLoop.current().spawn_callback(
            self.call_api, API_METHODS[action], id_, **data)

    def on_connection_close(self):
        if self.username:
            if self in GeopointClient.online_users[self.username]:
                GeopointClient.online_users[self.username].remove(self)

    def generate_success(self, id_, code='GENERIC_SUCCESS', data: Any = None):
        self.write_message({
            'id': id_,
            'status': 'success',
            'code': code,
            'data': data or {}
        })

    def generate_error(self, id_, code='GENERIC_ERROR', data: Any = None):
        self.write_message({
            'id': id_,
            'status': 'fail',
            'code': code,
            'data': data or {}
        })

    @classmethod
    def clear_old_activations(cls):
        cls.outgoing_activations = {
            key: activation
            for key, activation in cls.outgoing_activations.items()
            if perf_counter() - activation.time < 15 * 60
        }

    @register_api
    async def get_time(self, id_):
        self.generate_success(id_, data=IOLoop.current().time())

    @register_api
    @require_auth
    async def geopoint_get(self, id_):
        result = [
            {
                'lat': hit['lat'],
                'lon': hit['lon'],
                'time': hit['time']
            }
            for hit in database_client.local.points.find({'username': self.username})
        ]
        self.generate_success(id_, data=result)

    @register_api
    @require_auth
    async def geopoint_get_friends(self, id_):
        result = []

        for friend_name in await get_friend_list(self.username):
            result.extend(
                {
                    'lat': hit['lat'],
                    'lon': hit['lon'],
                    'time': hit['time'],
                    'friend': friend_name
                }
                for hit in database_client.local.points.find({'username': friend_name})
            )
        self.generate_success(id_, data=result)

    @register_api
    @require_auth
    async def geopoint_post(self, id_, lat=None, lon=None):
        new_point = {
            'username': self.username,
            'time': IOLoop.current().time(),
            'lat': lat,
            'lon': lon
        }

        points = list(database_client.local.points.find({
            'username': self.username
        }))

        if len(points) > 20000:
            self.generate_error(id_, 'TOO_MANY_POINTS')
            return

        database_client.local.points.insert_one(new_point)
        points.append(new_point)

        database_client.local.points.delete_many({
            'time': {
                # Geopoints persist only 2 hours.
                '$lt': IOLoop.current().time() - 7200
            }
        })

        self.generate_success(id_)

    @register_api
    @require_auth
    async def send_friend_request(self, id_, target=None):
        if self.username == target:
            self.generate_error(id_, 'FRIENDS_WITH_YOURSELF')
        elif not await user_in_db(target):
            self.generate_error(id_, 'USER_DOES_NOT_EXIST', data=target)
        elif target in self.outgoing_friend_requests[self.username]:
            self.generate_error(id_, 'REPEAT_FRIEND_REQUEST', data=target)
        elif target in await get_friend_list(self.username):
            self.generate_error(id_, 'ALREADY_FRIENDS', data=target)
        else:
            self.outgoing_friend_requests[self.username].append(target)
            self.inbound_friend_requests[target].append(self.username)
            self.generate_success(id_, data=target)

            if target in self.online_users:
                for client in self.online_users[target]:
                    client.generate_success(-1,
                                            'FRIEND_REQUEST', data=self.username)

    @register_api
    @require_auth
    async def delete_friend(self, id_, target=None):
        if self.username == target:
            self.generate_error(id_, 'REMOVE_YOURSELF')
        elif not await user_in_db(target):
            self.generate_error(id_, 'USER_DOES_NOT_EXIST', data=target)
        elif target not in await get_friend_list(self.username):
            self.generate_error(id_, 'ALREADY_NOT_FRIENDS', data=target)
        else:
            database_client.local.friendpairs.delete_one({
                '$or': [
                    {
                        'username1': self.username,
                        'username2': target
                    },
                    {
                        'username1': target,
                        'username2': self.username
                    }
                ]
            })
            self.generate_success(id_, data=target)

            if target in self.online_users:
                for client in self.online_users[target]:
                    client.generate_success(-1,
                                            'FRIEND_LIST_CHANGED', data=self.username)

            if self.username in self.online_users:
                for client in self.online_users[self.username]:
                    client.generate_success(-1,
                                            'FRIEND_LIST_CHANGED', data=self.username)

    @register_api
    @require_auth
    async def accept_friend_request(self, id_, target=None):
        if not await user_in_db(target):
            self.generate_error(id_, 'USER_DOES_NOT_EXIST', data=target)
            return
        if self.username not in self.outgoing_friend_requests[target]:
            self.generate_error(
                id_, 'USER_NOT_SENT_FRIEND_REQUEST', data=target)
            return

        database_client.local.friendpairs.insert_one({
            'username1': target,
            'username2': self.username
        })
        self.generate_success(id_, data=target)

        if target in self.online_users:
            for client in self.online_users[target]:
                client.generate_success(
                    '-1', 'FRIEND_LIST_CHANGED', data=self.username)

        if self.username in self.online_users:
            for client in self.online_users[self.username]:
                client.generate_success(-1,
                                        'FRIEND_LIST_CHANGED', data=self.username)
                client.generate_success(-1,
                                        'FRIEND_REQUEST_LIST_CHANGED', data=target)

        self.outgoing_friend_requests[target].remove(self.username)
        self.inbound_friend_requests[self.username].remove(target)

    @register_api
    @require_auth
    async def decline_friend_request(self, id_, target=None):
        if not await user_in_db(target):
            self.generate_error(id_, 'USER_DOES_NOT_EXIST', data=target)
            return
        if self.username not in self.outgoing_friend_requests[target]:
            self.generate_error(
                id_, 'USER_NOT_SENT_FRIEND_REQUEST', data=target)
            return

        self.generate_success(id_, data=target)
        if self.username in self.online_users:
            for client in self.online_users[self.username]:
                client.generate_success(-1,
                                        'FRIEND_REQUEST_LIST_CHANGED', data=target)

        self.outgoing_friend_requests[target].remove(self.username)
        self.inbound_friend_requests[self.username].remove(target)

    @register_api
    @require_auth
    async def get_my_friends(self, id_):
        self.generate_success(id_, data=await get_friend_list(self.username))

    @register_api
    @require_auth
    async def get_user_info(self, id_, target=None, time_frame=None):
        if not await user_in_db(target):
            self.generate_error(id_)
        else:
            points = list(database_client.local.points.find({
                'username': self.username,
                'time': {
                    '$gt': IOLoop.current().time() - time_frame
                }
            }))

            if len(points) > 1:
                points.sort(key=itemgetter('time'))

                distance_deltas = [
                    approximate_distance(
                        alpha['lat'], alpha['lon'], beta['lat'], beta['lon'])
                    for alpha, beta in zip(points[:-1], points[1:])
                ]

                time_deltas = [
                    beta['time'] - alpha['time']
                    for alpha, beta in zip(points[:-1], points[1:])
                ]

                speed_points = [
                    dst / tm
                    for dst, tm in zip(distance_deltas, time_deltas)
                ]

                database_client.local.users.update_one({
                    'username': self.username
                }, {
                    '$set': {
                        'total_distance': sum(distance_deltas),
                        'avg_speed': sum(speed_points) / len(speed_points)
                    }
                })

            user = database_client.local.users.find_one({
                'username': target
            })
            self.generate_success(id_, data={
                'target': target,
                'email': user['email'],
                'avg_speed': user['avg_speed'],
                'total_distance': user['total_distance']
            })

    @register_api
    @require_auth
    async def set_avatar(self, id_, data=None, extension=None):
        data = bytes(data)
        with open(path_join(getcwd(), 'avatars', f'{self.username}.{extension}'), 'wb') as output:
            print(data, file=output)
        self.generate_success(id_)

    @register_api
    @require_auth
    async def get_friend_requests(self, id_):
        self.generate_success(
            id_, data=self.inbound_friend_requests[self.username])

    @register_api
    @require_auth
    async def is_user_online(self, id_, target=None):
        self.generate_success(id_, data=target in self.online_users)

    @register_api
    async def register(self, id_, username=None, password=None, email=None):
        self.clear_old_activations()

        if any(
            (activation.username == username or activation.email == email)
            for activation in self.outgoing_activations.values()
        ):
            self.generate_error(id_, 'ACTIVATION_IN_PROGRESS')
        elif await user_in_db(username):
            self.generate_error(id_, 'USER_ALREADY_EXISTS', data=username)
        else:
            generated_key = ''.join(choice(digits) for _ in range(6))
            self.outgoing_activations[generated_key] = Activation(
                username, password, email
            )

            EMailSender.send_mail(
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
            self.generate_success(id_)

    @register_api
    async def activate(self, id_, key=None):
        self.clear_old_activations()

        if key not in self.outgoing_activations:
            self.generate_error(id_, 'INVALID_KEY')
        else:
            activation = self.outgoing_activations[key]
            database_client.local.users.insert_one({
                'username': activation.username,
                'password': activation.password,
                'email': activation.email,
                'speed_points': [0],
                'total_distance': 0
            })
            self.generate_success(id_)
            del self.outgoing_activations[key]


app = Application(
    [
        ('/websocket/([a-zA-Z0-9_]+)/([a-f0-9]{64})',
         GeopointClient, {'guest_session': False}),
        ('/websocket', GeopointClient, {'guest_session': True}),
        ('/avatar/(.+)', StaticFileHandler,
         {'path': path_join(getcwd(), 'avatars')}),
    ],
    websocket_ping_interval=5,
    websocket_ping_timeout=300
)

try:
    mkdir('avatars')
except IOError:
    pass

app.listen(8010)

print('The server is up')

IOLoop.current().start()
