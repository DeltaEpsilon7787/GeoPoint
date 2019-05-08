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
    conn = yield websocket_connect('ws://localhost:8010/', io_loop=IOLoop.instance())
    yield conn.write_message(dumps({
        'action': 'auth',
        'username': 'admin',
        'password': '9c237c21540f359825dd94f9939c3cd397613850abb8b915d3adf69046b34a37'
    }))
    result = yield conn.read_message()
    result = loads(result)
    print(result)

    session_id = result['session_id']
    yield conn.write_message(dumps({
        'action': 'geopoint_get',
        'username': 'admin',
        'session_id': session_id
    }))
    result = yield conn.read_message()
    print(result)

    # DoS test
    print('Alpha')
    yeet = [
        conn.write_message(dumps({
                'action': 'geopoint_get',
                'username': 'admin',
                'session_id': session_id
        }))
        for i in range(100000)
    ]
    print('Beta')
    start_time = perf_counter()
    yield multi(yeet)
    print('Gamma')
    result = yield conn.read_message()
    print('Delta')
    print(perf_counter() - start_time)

IOLoop.current().run_sync(test)