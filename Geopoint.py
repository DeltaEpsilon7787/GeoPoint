# coding: utf-8
from collections import defaultdict
from functools import wraps
from json import dumps, loads
from random import choice
from smtplib import SMTP_SSL
from string import ascii_letters
from time import perf_counter, time

import pymongo
from tornado.gen import coroutine
from tornado.ioloop import IOLoop
from tornado.web import Application, RequestHandler
from tornado.websocket import WebSocketHandler

database_client = pymongo.MongoClient(host='localhost', port=27017)
email_client = SMTP_SSL(host='smtp.gmail.com',
                        port=465)
email_client.login('scrapebot.test@gmail.com', 'alpha_beta')


class InvalidSignatureError(Exception):
    pass


API_METHODS = {}
class GeopointServer(WebSocketHandler):
    active_sessions = {}
    last_clear = time()

    outgoing_activations = {}
    outgoing_friend_reqs = defaultdict(list)

    notify_friend_req_response = defaultdict(list)

    def generate_success(self, id_, code='GENERIC_SUCCESS', data={}):
        return self.write_message({
            'id': id_,
            'status': 'success',
            'code': code,
            'data': data
        })

    def generate_error(self, id_, code='GENERIC_ERROR', data={}):
        return self.write_message({
            'id': id_,
            'status': 'fail',
            'code': code,
            'data': data
        })

    def assert_active(func):
        @wraps(func)
        def inner(self, id, **params):
            try:
                params['username']
                params['session_id']
            except KeyError:
                self.generate_error(id, 'AUTH_DENIED')
                return False

            if params['username'] not in self.active_sessions:
                self.generate_error(params.get('id'), 'USER_NOT_LOGGED')
                return False

            if params['session_id'] != self.active_sessions[username][1]:
                self.generate_error(params.get('id'), 'SESSION_EXPIRED')
                return False
            return func(**params)
        return inner

    def register_api(*signature):
        def decorator(func):
            @wraps(func)
            def inner(self, id, **params):
                params = {
                    key: value
                    for key, value in params.items()
                    if value is not None
                }
                if {*params.keys()} < {*signature}:
                    raise InvalidSignatureError
                return func(self, id, **{
                    key: params[key]
                    for key in signature
                })
            API_METHODS[func.__name__] = inner
            return inner
        return decorator

    @staticmethod
    def check_login(username, password):
        return database_client.local.users.find_one({
            'username': username,
            'password': password
        })

    @staticmethod
    def user_in_db(username):
        return database_client.local.users.find_one({
            'username': username
        })

    @staticmethod
    def get_friend_list(username):
        return list(database_client.local.friendpairs.find({
            'username1': username
        })) + list(database_client.local.friendpairs.find({
            'username2': username
        }))

    @staticmethod
    def clear_old_activations():
        for key, (_, time, _, _) in GeopointServer.outgoing_activations.items():
            if perf_counter() - time > 15 * 60:
                del GeopointServer.outgoing_activations[key]

    @register_api()
    def get_time(self, id):
        self.generate_success(id, data=time())

    @coroutine
    @assert_active
    @register_api('username')
    def geopoint_get(self, id, username=None):
        result = [
            {
                'lat': hit['lat'],
                'lon': hit['lon'],
                'time': hit['time']
            }
            for hit in database_client.local.points.find({'username': username})
        ]
        self.generate_success(id, data=result)

    @coroutine
    @assert_active
    @register_api('username')
    def geopoint_get_friends(self, id, username=None):
        result = []

        for friend_datum in self.get_friend_list(username):
            friend_name = (
                friend_datum['username2']
                if friend_datum['username1'] == username
                else friend_datum['username1']
                if friend_datum['username1'] != username
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

    @coroutine
    @assert_active
    @register_api('lat', 'lon', 'username')
    def geopoint_post(self, id, lat=None, lon=None, username=None):
        database_client.local.points.insert_one({
            'username': username,
            'time': time(),
            'lat': lat,
            'lon': lon
        })
        self.generate_success(id)

    @coroutine
    @assert_active
    @register_api('username', 'target')
    def send_friend_request(self, id, username=None, target=None):
        if username == target:
            self.generate_error(id, 'FRIENDS_WITH_YOURSELF')
        elif not self.user_in_db(target):
            self.generate_error(id, 'USER_DOES_NOT_EXIST', data=target)
        elif target in self.outgoing_friend_reqs[username]:
            self.generate_error(id, 'REPEAT_FRIEND_REQUEST', data=target)
        else:
            self.outgoing_friend_reqs[username].append(target)
            self.generate_success(id, data=target)

    @coroutine
    @assert_active
    @register_api('username', 'target', 'is_accept')
    def respond_to_friend_request(self, id, username=None, target=None, is_accept=None):
        if not self.user_in_db(target):
            self.generate_error(id, 'USER_DOES_NOT_EXIST', data=target)
            return
        if username not in self.outgoing_friend_reqs[target]:
            self.generate_error(
                id, 'USER_NOT_SENT_FRIEND_REQUEST', data=target)
            return

        if is_accept:
            database_client.local.friendpairs.insert({
                'username1': target,
                'username2': username
            })
            self.generate_success(id, 'FRIEND_ADDED', data=target)
        else:
            self.generate_success(id, 'FRIEND_NOT_ADDED', data=target)
        self.notify_friend_req_response[target].append(
            (username, is_accept))
        self.outgoing_friend_reqs[target].remove(username)

    @coroutine
    @assert_active
    @register_api('username')
    def get_my_friends(self, id, username=None):
        self.generate_success(id, data=[
            friend_datum['username2']
            if friend_datum['username1'] == username
            else friend_datum['username1']
            for friend_datum in self.get_friend_list(username)
        ])

    @coroutine
    @register_api('username', 'password', 'email')
    def register(self, id, username=None, password=None, email=None):
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

    @coroutine
    @register_api('key')
    def activate(self, id, key=None):
        self.clear_old_activations()

        if key not in self.outgoing_activations:
            self.generate_error(id, 'INVALID_KEY')
        else:
            email, _, username, password = GeopointServer.outgoing_activations[key]
            database_client.local.users.insert_one({
                'username': username,
                'password': password,
                'email': email
            })
            self.generate_success(id)
            del GeopointServer.outgoing_activations[key]

    @assert_active
    @register_api('username', 'session_id')
    def accept_heartbeat(self, id, username=None, session_id=None):
        if self.last_clear - time() > 5 * 60:
            self.last_clear = time()
            for key, (last_ping, _) in self.active_sessions.items():
                if last_ping - time() > 2 * 60:
                    del self.active_sessions[key]

        if session_id == self.active_sessions[username][1]:
            self.active_sessions[username][0] = time()

        self.generate_success(id)

    @coroutine
    @assert_active
    @register_api('username')
    def get_friend_requests(self, id, username=None):
        wannabe_friends = [
            wannabe_friend
            for wannabe_friend, requests in self.outgoing_friend_reqs.items()
            if username in requests
        ]

        self.generate_success(id, data=wannabe_friends)

    @coroutine
    @register_api('username', 'password')
    def auth(self, id, username=None, password=None):
        if self.check_login(username, password):
            generated_id = ''.join(choice(ascii_letters) for _ in range(50))
            self.active_sessions[username] = [time(), generated_id]
            self.generate_success(id, data=generated_id)
        else:
            self.generate_error(id)

    @coroutine
    def on_message(self, message):
        print(message)
        if len(message) > 10000:
            self.generate_error(-1, 'MESSAGE_TOO_LONG')
            return

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


app = Application([('/', GeopointServer)])

app.listen(8010)

print('The server is up')
print(API_METHODS)

IOLoop.current().start()
