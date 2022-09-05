import asyncio
import collections
import logging
import struct
import time
import zlib

import aiohttp
import brotli
import requests

from config import Operation
from entity import *

HEADER_STRUCT = struct.Struct('>I2H2I')
# > 表示大端模式
# I: 无符号整型 32bit, H: 无符号短整型，16bit
# package 固定头部 16 bytes, 参考 https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/live/message_stream.md
# uint32 总大小(单位bytes) | uint16 头部大小(单位bytes) | uint16 协议版本(判断压缩类型) | uint32 操作码 | uint32 序列
HeaderTuple = collections.namedtuple('HeaderTuple', ('pack_len', 'raw_header_size', 'ver', 'operation', 'seq_id'))

logger = logging.getLogger('bilibili')
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36"
}


async def make_package(data: dict, operate: int) -> bytes:
    """
    生成package
    :param data: json数据
    :param operate: 操作码
    :return: package
    """
    body = json.dumps(data).encode('utf-8')
    header = HEADER_STRUCT.pack(*HeaderTuple(
        pack_len=HEADER_STRUCT.size + len(body),
        raw_header_size=HEADER_STRUCT.size,
        ver=1,
        operation=operate,
        seq_id=1
    ))
    # print(header + body)
    return header + body


async def send_auth(room_id, uid=0, token=None):
    """
    合成auth package
    :param room_id: 真实room_id 非短id，类似123
    :param uid: 默认0，用户id
    :param token: 默认空
    :return: package
    """
    auth_params = {
        'uid': uid,
        'roomid': room_id,
        'protover': 3,
        'platform': 'web',
        'type': 2
    }
    if token is not None:
        auth_params['key'] = token
    return await make_package(auth_params, Operation.AUTH)


async def send_heartbeat(ws):
    """
    发送心跳包, 每隔30s
    :param ws: websocket对象
    :return:
    """
    while True:
        try:
            pkg = await make_package({}, Operation.HEARTBEAT)
            await ws.send_bytes(pkg)
            print("======SEND HEARTBEAT=====")
        except (ConnectionResetError, aiohttp.ClientConnectionError) as e:
            logger.warning('send_heartbeat() failed: %r', e)
        except Exception:  # noqa
            logger.exception('send_heartbeat() failed:')
        await asyncio.sleep(30)


async def receive_package(websocket):
    """
    每隔1s获取package
    :param websocket: websocket对象
    :return:
    """
    while True:
        await asyncio.sleep(1)
        receive_data = await websocket.receive()
        # print(receive_data)
        if receive_data is not None:
            await parse_package(receive_data.data, 0)
            pass


async def parse_package(data, offset):
    """
    解析package
    :param data: 接收的package
    :param offset: 偏移
    :return:
    """
    header = HeaderTuple(*HEADER_STRUCT.unpack_from(data, offset))
    if header.operation in (Operation.SEND_MSG_REPLY, Operation, Operation.AUTH_REPLY):
        # 属于弹幕或者认证回复
        while True:
            # 可能多个包一起发
            body = data[offset + header.raw_header_size: offset + header.pack_len]
            await parse_data(header, body)

            offset += header.pack_len
            if offset >= len(data):  # 看是否只有一个包
                break
            header = HeaderTuple(*HEADER_STRUCT.unpack_from(data, offset))

    elif header.operation == Operation.HEARTBEAT_REPLY:
        body = data[offset + header.raw_header_size: offset + header.raw_header_size + 4]
        popularity = int.from_bytes(body, 'big')
        body = {
            'cmd': '_HEARTBEAT',
            'data': {
                'popularity': popularity
            }
        }
    else:
        # 未知消息
        body = data[offset + header.raw_header_size: offset + header.pack_len]
        logger.warning('unknown message operation=%d, header=%s, body=%s',
                       header.operation, header, body)


async def parse_data(header, body):
    """
    解析具体内容
    :param header:
    :param body:
    :return:
    """
    if header.operation == Operation.SEND_MSG_REPLY:
        if header.ver == 3:  # brotli压缩格式
            body = brotli.decompress(body)
            await parse_package(body, 0)
        elif header.ver == 2:
            body = zlib.decompress(body)
            await parse_package(body, 0)

        elif header.ver == 0:
            body = json.loads(body.decode('utf-8'))
            await print_msg(body)

        else:
            # 未知格式
            logger.warning('unknown protocol version=%d, header=%s, body=%s',
                           header.ver, header, body)
    elif header.operation == Operation.AUTH_REPLY:
        # 认证响应
        body = json.loads(body.decode('utf-8'))
        if body['code'] != 0:
            print("认证失败！")
            exit(0)
        # await print_msg(body)
        print(body)
        # await send_bytes(make_package({}, Operation.HEARTBEAT))
    else:
        # 未知消息
        logger.warning('unknown message operation=%d, header=%s, body=%s',
                       header.operation, header, body)


async def print_msg(body):
    # print(body)
    if body['cmd'] == 'DANMU_MSG':
        msg = DanmakuMessage.from_command(body['info'])
        if msg.emoticon_options_dict == {}:
            print(
                f'【{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp / 1000))}】【{msg.uname}】: {msg.msg}')
        else:
            print(
                f'【{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp / 1000))}】【{msg.uname}】: {msg.msg} [is_emoji: {msg.emoticon_options_dict}]')

    if body['cmd'] == 'SEND_GIFT':
        gift = GiftMessage.from_command(body['data'])
        print(
            f'【{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gift.timestamp))}】【{gift.uname}】送了: 【{gift.gift_name}*{gift.num}】')

    if body['cmd'] == 'GUARD_BUY':
        guard = GuardBuyMessage.from_command(body['data'])
        print(
            f'【【【{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(guard.start_time))}】】】【{guard.username}】送了大航海: 【{guard.gift_name}*{guard.num}】')
    if body['cmd'] == 'SUPER_CHAT_MESSAGE':
        sc = SuperChatMessage.from_command(body['data'])
        print(
            f'SC: [{sc.price}RMB] 【{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sc.start_time))}】【{sc.uname}】SC内容: 【{sc.message}】')

    if body['cmd'] == 'INTERACT_WORD':
        entry = body['data']
        print(f"有人进入：【{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['timestamp']))}】【{entry['uname']}】")

    if body['cmd'] == 'WELCOME_GUARD':
        entry = body['data']
        print(
            f"舰长进入：【{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['timestamp']))}】【{entry['uname']}】{entry}")
        print(f"WELCOME_GUARD 舰长 进入：{entry}")

    if body['cmd'] == 'ENTRY_EFFECT':
        entry = body['data']
        # print(f"ENTRY_EFFECT 舰长,高能榜 进入：【{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['timestamp']))}】【{entry['uname']}】{entry}")
        print(f"ENTRY_EFFECT 舰长,高能榜 进入：{entry}")


async def ws_rec(ws):
    pkg = await ws.receive()
    await parse_package(pkg.data, 0)


async def startup(uri, room_id, token):
    ws = await aiohttp.ClientSession().ws_connect(uri, headers=headers, ssl=True)
    await ws.send_bytes(await send_auth(room_id, token=token))
    await ws_rec(ws)
    task1 = receive_package(ws)
    task2 = send_heartbeat(ws)
    await asyncio.gather(task2, task1)
    await ws.close()


def get_live_info(room_id):
    """
    获取真实room_id,短room_id 并不是实际使用的
    :param room_id:
    :return: real_room_id, danmuku_server_url
    """
    global token
    res = requests.get(f"https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}").json()
    real_room_id = res['data']['room_id']
    info_stream = requests.get(
        f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id={real_room_id}&type=0").json()
    token = info_stream['data']['token']
    return f"wss://{info_stream['data']['host_list'][0]['host']}:{info_stream['data']['host_list'][0]['wss_port']}/sub", real_room_id, token


if __name__ == '__main__':
    url, room_id, token = get_live_info(545)
    asyncio.get_event_loop().run_until_complete(startup(url, room_id, token))
