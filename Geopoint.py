# coding: utf-8
from collections import defaultdict
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


def generate_response(source, action, status, reason):
    return source.write_message({
        'action': action,
        'status': status,
        'reason': reason
    })


class AuthRegisterHandler(RequestHandler):
    @coroutine
    def get(self, email, key):
        GeopointServer.clear_old_activations()
        if email not in GeopointServer.outgoing_activations:
            self.write('This email is not in the process of activation or the key expired.')
        elif GeopointServer.outgoing_activations[email][0] != key:
            self.write('Incorrect activation key.')
        else:
            _, _, username, password = GeopointServer.outgoing_activations[email]
            database_client.local.users.insert_one({
                'username': username,
                'password': password,
                'email': email
            })
            self.write('Your account has successfully been activated.')


class GeopointServer(WebSocketHandler):
    active_sessions = {}
    last_clear = time()

    outgoing_activations = {}
    outgoing_friend_reqs = defaultdict(list)

    notify_friend_req_response = defaultdict(list)

    @coroutine
    def geopoint_get(self, username=None, session_id=None):
        if username not in self.active_sessions:
            generate_response(self, 'geopoint_get', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'geopoint_get', 'fail', 'Session has expired.')
            return

        result = [
            {
                'lat': hit['lat'],
                'lon': hit['lon'],
                'time': hit['time']
            }
            for hit in database_client.local.points.find({'username': username})
        ]

        generate_response(self, 'geopoint_get', 'success', dumps(result))

    @coroutine
    def geopoint_get_friends(self, username=None, session_id=None):
        if username not in self.active_sessions:
            generate_response(self, 'geopoint_get_friends', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'geopoint_get_friends', 'fail', 'Session has expired.')
            return

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
        generate_response(self, 'geopoint_get', 'success', dumps(result))

    @coroutine
    def geopoint_post(self, lat=None, lon=None, username=None, session_id=None):
        if username not in self.active_sessions:
            generate_response(self, 'geopoint_post', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'geopoint_post', 'fail', 'Session has expired.')
            return

        database_client.local.points.insert_one({
            'username': username,
            'time': time(),
            'lat': lat,
            'lon': lon
        })

        generate_response(self, 'geopoint_post', 'success', 'Geopoint has been added.')

    @coroutine
    def send_friend_request(self, username=None, session_id=None, target=None):
        if username == target:
            generate_response(self, 'send_friend_request', 'fail', 'You are already friends with yourself')

        if username not in self.active_sessions:
            generate_response(self, 'send_friend_request', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'send_friend_request', 'fail', 'Session has expired.')
            return

        if not self.user_in_db(target):
            generate_response(self, 'send_friend_request', 'fail', 'This user does not exist.')
            return

        if target in self.outgoing_friend_reqs[username]:
            generate_response(self, 'send_friend_request', 'fail',
                              f'You have already send a friend request to {target}')
            return

        self.outgoing_friend_reqs[username].append(target)
        generate_response(self, 'send_friend_request', 'success', f'Your friend request has been sent to {target}')

    @coroutine
    def respond_to_friend_request(self, username=None, session_id=None, target=None, is_accept=None):
        if username not in self.active_sessions:
            generate_response(self, 'respond_to_friend_request', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'respond_to_friend_request', 'fail', 'Session has expired.')
            return

        if not self.user_in_db(target):
            generate_response(self, 'respond_to_friend_request', 'fail', 'This user does not exist.')
            return

        if username not in self.outgoing_friend_reqs[target]:
            generate_response(self, 'respond_to_friend_request', 'fail', f'This user has not sent you a friend request')
            return

        if is_accept:
            database_client.local.friendpairs.insert({
                'username1': target,
                'username2': username
            })
            generate_response(self, 'respond_to_friend_request', 'success', f'You have added {target} to friends.')
        else:
            generate_response(self,
                              'respond_to_friend_request',
                              'success',
                              f"You have declined {target}'s friend request")
        self.notify_friend_req_response[target].append((username, is_accept))
        self.outgoing_friend_reqs[target].remove(username)

    @coroutine
    def get_my_friends(self, username=None, session_id=None):
        if username not in self.active_sessions:
            generate_response(self, 'get_my_friends', 'fail', 'User has not logged in.')
            return

        if session_id != self.active_sessions[username][1]:
            generate_response(self, 'get_my_friends', 'fail', 'Session has expired.')
            return

        generate_response(self,
                          'get_my_friends',
                          'success',
                          dumps(
                              friend_datum['username2']
                              if friend_datum['username1'] == username
                              else friend_datum['username1']
                              for friend_datum in self.get_friend_list(username)
                          ))

    @staticmethod
    @coroutine
    def clear_old_activations():
        for key, (_, time, _, _) in GeopointServer.outgoing_activations.items():
            if perf_counter() - time > 15 * 60:
                del GeopointServer.outgoing_activations[key]

    @coroutine
    def register(self, username=None, password=None, email=None):
        self.clear_old_activations()

        if email in self.outgoing_activations:
            generate_response(self, 'register', 'fail', 'An activation message has already been sent to this email.')
        elif self.user_in_db(username):
            generate_response(self, 'register', 'fail', 'This user already exists.')
        else:
            generated_key = ''.join(choice(ascii_letters) for _ in range(50))
            self.outgoing_activations[email] = (generated_key, perf_counter(), username, password)

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
                    "To activate your account, head over to this link %TODO%\n"
                    f"Alternatively, you can enter this key: {generated_key}"
                )
            )

            generate_response(self, 'register', 'success',
                              'An activation message has been sent to this email. You have 15 minutes to accept it.')

    @coroutine
    def get_stat(self, username=None, session_id=None):
        if self.last_clear - time() > 5 * 60:
            self.last_clear = time()
            for key, (last_ping, _) in self.active_sessions.items():
                if last_ping - time() > 2 * 60:
                    del self.active_sessions[key]

        if username in self.active_sessions:
            if session_id == self.active_sessions[username][1]:
                self.active_sessions[username][0] = time()

                wannabe_friends = [
                    wannabe_friend
                    for wannabe_friend, requests in self.outgoing_friend_reqs.items()
                    if username in requests
                ]

                answer = {
                    'pending_friend_requests': wannabe_friends
                }

                generate_response(self, 'get_stat', 'success', dumps(answer))
        else:
            generate_response(self, 'get_stat', 'fail', 'Session does not exist.')

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

    @coroutine
    def auth(self, username=None, password=None):
        if self.check_login(username, password):
            generated_id = ''.join(choice(ascii_letters) for _ in range(50))
            self.active_sessions[username] = [time(), generated_id]
            self.write_message({
                'action': 'auth',
                'status': 'success',
                'session_id': generated_id,
                'reason': 'Authentication successful.'
            })
        else:
            generate_response(self, 'auth', 'fail', 'Incorrect username or password.')

    @coroutine
    def on_message(self, message):
        print(message)
        if len(message) > 10000:
            generate_response(self, 'any', 'fail', 'This message is too long')
            return

        try:
            data = loads(message)
        except Exception as E:
            generate_response(self, 'any', 'fail', 'JSON decode error')
            return

        if 'action' not in data:
            generate_response(self, 'any', 'fail', 'Action is not defined')
            return

        action = data['action']

        try:
            func = getattr(GeopointServer, action)
            getattr(func, '__code__')
            getattr(func.__code__, 'co_varnames')
        except AttributeError:
            generate_response(self, 'any', 'fail', 'This action does not exist')
            return

        args = {*data}

        if hasattr(func, '__wrapped__'):
            aux_args = {*func.__wrapped__.__code__.co_varnames} - {'self'}
        else:
            aux_args = {*func.__code__.co_varnames} - {'self'}
        if args < aux_args:
            generate_response(self, action, 'fail', 'Not enough arguments')
            return

        try:
            yield func(self, **{
                arg: value
                for arg, value in data.items()
                if arg in aux_args
            })
        except Exception as E:
            print(
                E)
            generate_response(self, action, 'fail', 'Unknown error')


app = Application([
    ('/activate/(.+?)/(.+?)$', AuthRegisterHandler),
    ('/', GeopointServer)
])

app.listen(8010)

print('The server is up')
IOLoop.current().start()
