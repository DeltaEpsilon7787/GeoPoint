from json import dumps, loads

from tornado.gen import coroutine
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

@coroutine
def test():
    conn = yield websocket_connect('ws://31.25.28.142:8010/websocket')
    # conn = yield websocket_connect('ws://localhost:8010/websocket')

    response = yield conn.read_message()
    print(response)
    def send(obj):
        yield conn.write_message(dumps(obj))
        result = yield conn.read_message()
        print(result)

    yield from send({
        'id': 'Register',
        'action': 'register',
        'username': 'tester',
        'password': '187c6c9e881d33ab9c94cb369d76f8d16e505143bd6fedbfe80ccf3f413d98d2',
        'email': 'b1512549@urhen.com'
    })

    yield from send({
        'id': 'Time',
        'action': 'get_time'
    })

    yield from send({
        'id': 'Geopoint Get',
        'action': 'geopoint_get',
    })

    yield from send({
        'id': 'Geopoint Get Friends',
        'action': 'geopoint_get_friends',
    })

    yield from send({
        'id': 'Geopoint Post',
        'action': 'geopoint_post',
        'lat': 0.5,
        'lon': 0.5
    })

    yield from send({
        'id': 'Geopoint Send Friend',
        'action': 'send_friend_request',
        'target': 'test_account'
    })

    yield from send({
        'id': 'Responding to friend A',
        'action': 'accept_friend_request',
        'target': 'test_account',
    })

    yield from send({
        'id': 'Responding to friend B',
        'action': 'decline_friend_request',
        'target': 'test_account',
    })

IOLoop.current().run_sync(test)