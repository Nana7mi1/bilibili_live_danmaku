import asyncio
import collections
import json
import struct
from aiowebsocket.converses import AioWebSocket

url = 'wss://tx-bj-live-comet-02.chat.bilibili.com:2245/sub'
uid = 0

auth_params = {
    'uid': uid,
    'roomid': 22676119,
    'protover': 3,
    'platform': 'web',
    'type': 2
}

heart_params = {}

HEADER_STRUCT = struct.Struct('>I2H2I')
# > 表示大端模式
# I: 无符号整型 32bit, H: 无符号短整型，16bit
# package 固定头部 10bytes, 参考 https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/live/message_stream.md
# uint32 总大小(单位bytes) | uint16 头部大小(单位bytes) | uint16 协议版本(判断压缩类型) | uint32 操作码 | uint32 序列
HeaderTuple = collections.namedtuple('HeaderTuple', ('pack_len', 'raw_header_size', 'ver', 'operation', 'seq_id'))


def make_pkg(data, opt):
    body = json.dumps(data).encode('utf-8')
    header = HEADER_STRUCT.pack(*HeaderTuple(
        pack_len=HEADER_STRUCT.size + len(body),
        raw_header_size=HEADER_STRUCT.size,
        ver=1,
        operation=opt,
        seq_id=1
    ))
    return header + body


async def receive_pkg(websocket):
    # while True:
    receive_data = await websocket.receive()
    # if receive_data is None:
    #     receive_data = b'\x00\x00\x00\x1a\x00\x10\x00\x01\x00\x00\x00\x08\x00\x00\x00\x01{"code":0}'
    print(receive_data)
    # print(receive_data.hex())
    param_pkg(receive_data, 0)


def param_pkg(data, offset):
    header = HeaderTuple(*HEADER_STRUCT.unpack_from(data, offset))
    print(header)
    if header.operation == 3:
        body = data[offset + header.raw_header_size: offset + header.raw_header_size + 4]  # 手动截取人气值(16-20)字节
        popularity = int.from_bytes(body, 'big')  # 换成大端模式
        # 自己造个消息当成业务消息处理
        body = {
            'cmd': '_HEARTBEAT',
            'data': {
                'popularity': popularity
            }
        }
        print(body)


async def main():
    async with AioWebSocket(url) as aws:
        converse = aws.manipulator
        print(make_pkg(auth_params, 7))
        await converse.send(make_pkg(auth_params, 7))
        await receive_pkg(converse)
        print(make_pkg(heart_params, 2))
        await converse.send(make_pkg(heart_params, 2))
        await receive_pkg(converse)
        await receive_pkg(converse)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
