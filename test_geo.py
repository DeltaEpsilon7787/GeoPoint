import asyncio

from json import dumps, loads
from random import choice
from smtplib import SMTP_SSL
from string import ascii_letters
from time import perf_counter, time

import pymongo
import tornado

from tornado.gen import coroutine, multi
from tornado.ioloop import IOLoop
from tornado.web import Application, RequestHandler
from tornado.websocket import WebSocketHandler, websocket_connect

@coroutine
def test():
    # conn = yield websocket_connect('ws://31.25.28.142:8010/')
    conn = yield websocket_connect('ws://localhost:8010/')
    yield conn.write_message(dumps({
        'id': 5,
        'action': 'auth',
        'username': 'admin',
        'password': '9c237c21540f359825dd94f9939c3cd397613850abb8b915d3adf69046b34a37'
    }))
    result = yield conn.read_message()
    result = loads(result)
    session_id = result['data']

    def send(obj):
        yield conn.write_message(dumps(obj))
        result = yield conn.read_message()
        print(loads(result))
    
    yield from send({
        'id': 'Time',
        'action': 'get_time',
        'junk': 'args'
    })

    yield from send({
        'id': 'Geopoint Get',
        'action': 'geopoint_get',
        'username': 'admin',
        'session_id': session_id
    })

    yield from send({
        'id': 'Geopoint Get Friends',
        'action': 'geopoint_get_friends',
        'username': 'admin',
        'session_id': session_id
    })

    yield from send({
        'id': 'Geopoint Post',
        'action': 'geopoint_post',
        'username': 'admin',
        'session_id': session_id,
        'lat': 0.5,
        'lon': 0.5
    })

    yield from send({
        'id': 'Geopoint Send Friend',
        'action': 'send_friend_request',
        'username': 'admin',
        'session_id': session_id,
        'target': 'test_account'
    })


    yield from send({
        'id': 'Responding to friend',
        'action': 'respond_to_friend_request',
        'username': 'admin',
        'session_id': session_id,
        'target': 'test_account',
        'is_accept': False
    })

    # while True:
    #     act = input('action: ')
    #     params = input('params: ')

    #     try:
    #         params = {
    #             pair.split(':')[0]: pair.split(':')[1]
    #             for pair in params.split(';')
    #         }
    #     except Exception as E:
    #         print(E)
    #         continue

    #     yield conn.write_message(dumps({
    #         'id': 0,
    #         'action': '',
    #         'username': 'admin',
    #         'session_id': session_id
    #     }))

    #     result = yield conn.read_message()
    #     result = loads(result)
    #     print(result)

    #     yield conn.write_message(dumps({
    #         'action': act,
    #         'id': 10,
    #         'username': 'admin',
    #         'session_id': session_id,
    #         **params
    #     }))

    #     result = yield conn.read_message()
    #     result = loads(result)
    #     print(f'{act}: {result}')
    # pass

IOLoop.current().run_sync(test)