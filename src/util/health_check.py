import os
from collections import ChainMap
from functools import reduce
from threading import Thread
from typing import List, Optional, Awaitable, Tuple

import tornado.ioloop
import tornado.options
import tornado.web

from src.contracts.secret.secret_contract import add_token
from src.db.collections.commands import Commands
from src.db.collections.eth_swap import Status
from src.leader.eth.leader import EtherLeader
from src.leader.secret20 import Secret20Leader
from src.signer.eth.signer import EtherSigner
from src.signer.secret20 import Secret20Signer
from src.util.secretcli import create_unsigned_tx
from src.util.web3 import w3


def get_health(threads: Tuple[EtherSigner, Secret20Signer, Optional[EtherLeader], Optional[Secret20Leader]]):
    health = dict(
        ChainMap(*list(map(lambda t: {t.getName(): {True: "pass", False: "fail"}[t.running()]}, threads))))
    overall = reduce(lambda x, y: x and y, map(lambda v: v[1] == "pass", health.items()))
    health.update({"overall": {True: "pass", False: "fail"}[overall]})
    try:
        config = threads[0].config
        balance = w3.eth.getBalance(config.eth_address, "latest")
        health.update({"eth-balance": str(w3.fromWei(balance, 'ether'))})
    except Exception:  # pylint: disable=broad-except
        health.update({"eth-balance": "failed to update"})
    return health


class MainHandler(tornado.web.RequestHandler):
    def __init__(self, application, request, **kwargs):
        self.threads: None = None
        super().__init__(application, request, **kwargs)

    def initialize(self, threads):
        self.threads: Tuple[EtherSigner, Secret20Signer, Optional[EtherLeader], Optional[Secret20Leader]] = threads

    def data_received(self, chunk: bytes) -> Optional[Awaitable[None]]:
        pass

    def get(self):
        health = get_health(self.threads)

        if health:
            self.finish(health)


class HealthSimpleHandler(tornado.web.RequestHandler):
    def __init__(self, application, request, **kwargs):
        self.threads: None = None
        super().__init__(application, request, **kwargs)

    def initialize(self, threads):
        self.threads: Tuple[EtherSigner, Secret20Signer, Optional[EtherLeader], Optional[Secret20Leader]] = threads

    def data_received(self, chunk: bytes) -> Optional[Awaitable[None]]:
        pass

    def get(self):
        health = get_health(self.threads)

        if health.get('overall', 'fail') != 'pass':
            raise tornado.web.HTTPError(status_code=500)
        self.finish(health)


class CommandHandler(tornado.web.RequestHandler):
    def __init__(self, application, request, **kwargs):
        self.threads: None = None
        super().__init__(application, request, **kwargs)

    def initialize(self, threads):
        self.threads: Tuple[EtherSigner, Secret20Signer, Optional[EtherLeader], Optional[Secret20Leader]] = threads

    def data_received(self, chunk: bytes) -> Optional[Awaitable[None]]:
        pass

    def get(self):
        if len(self.threads) < 4:
            raise tornado.web.HTTPError(status_code=400)

        key = self.get_argument('key')

        if key != os.getenv('BRIDGE_API_KEY'):
            raise tornado.web.HTTPError(status_code=401)

        token = self.get_argument('token')
        min_amount = int(self.get_argument('min_amount'))
        code_hash = self.get_argument('code_hash')
        unsigned = add_token(token, code_hash, min_amount)

        swap_contract = self.threads[3].config.scrt_swap_address
        swap_code_hash = self.threads[3].config.swap_code_hash
        multisig = self.threads[3].config.multisig_acc_addr
        chain_id = self.threads[3].config.chain_id
        enclave_key = self.threads[3].config.enclave_key

        tx = create_unsigned_tx(secret_contract_addr=swap_contract,
                                code_hash=swap_code_hash,
                                multisig_acc_addr=multisig,
                                enclave_key=enclave_key,
                                chain_id=chain_id,
                                transaction_data=unsigned)

        Commands(unsigned_tx=tx,
                 sequence=self.threads[3].manager.sequence,
                 dst_address=token,
                 status=Status.SWAP_UNSIGNED).save()

        self.threads[3].manager.sequence += 1


# class TestHandler(tornado.web.RequestHandler):
#     def __init__(self, application, request, **kwargs):
#         self.threads: None = None
#         super().__init__(application, request, **kwargs)
#
#     def initialize(self, threads):
#         self.threads: Tuple[EtherSigner, Secret20Signer, Optional[EtherLeader], Optional[Secret20Leader]] = threads
#
#     def data_received(self, chunk: bytes) -> Optional[Awaitable[None]]:
#         pass
#
#     def get(self):
#         self.threads[0].stop()


def make_app(threads):

    for t in threads:
        t.start()

    return tornado.web.Application([
        (r"/health", MainHandler, dict(threads=threads)),
        (r"/health_simple", HealthSimpleHandler, dict(threads=threads)),
        (r"/add_token", CommandHandler, dict(threads=threads)),
        # (r"/test", TestHandler, dict(threads=threads)),
    ])


def run(threads: List[Thread]):

    app = make_app(threads)
    app.listen(8888)
    tornado.ioloop.IOLoop.current().start()