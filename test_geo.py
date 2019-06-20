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
    print('?')
    result = yield conn.read_message()
    print('??')
    result = loads(result)
    print('???')
    session_id = result['data']

    while True:
        act = input('action: ')
        params = input('params: ')

        try:
            params = {
                pair.split(':')[0]: pair.split(':')[1]
                for pair in params.split(';')
            }
        except Exception as E:
            print(E)
            continue

        yield conn.write_message(dumps({
            'id': 0,
            'action': '',
            'username': 'admin',
            'session_id': session_id
        }))

        result = yield conn.read_message()
        result = loads(result)
        print(result)

        yield conn.write_message(dumps({
            'action': act,
            'id': 10,
            'username': 'admin',
            'session_id': session_id,
            **params
        }))

        result = yield conn.read_message()
        result = loads(result)
        print(f'{act}: {result}')
    pass


IOLoop.current().run_sync(test)