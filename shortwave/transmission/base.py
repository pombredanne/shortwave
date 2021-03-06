#!/usr/bin/env python
# coding: utf-8

# Copyright 2011-2016, Nigel Small
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from errno import ENOTCONN, EBADF
from logging import getLogger
from socket import socket as _socket, error as socket_error, \
    AF_INET, SOCK_STREAM, IPPROTO_TCP, TCP_NODELAY, SHUT_RD, SHUT_WR
from threading import Thread

from shortwave.concurrency import synchronized

log = getLogger("shortwave.transmission")

default_buffer_size = 524288


class BaseTransmitter(object):
    """ A Transmitter handles the outgoing half of a network conversation.
    Transmission is synchronous and will block until all data has been
    sent.
    """

    def __init__(self, socket, *args, **kwargs):
        self.socket = socket
        self.fd = self.socket.fileno()

    def transmit(self, *data):
        joined = b"".join(data)
        log.info("T[%d]: %s", self.fd, joined)
        self.socket.sendall(joined)


class BaseReceiver(Thread):
    """ A Receiver handles the incoming halves of one or more network
    conversations.
    """

    _stopped = False

    def __init__(self):
        super(BaseReceiver, self).__init__()
        self.clients = {}

    def __repr__(self):
        return "<%s at 0x%x>" % (self.__class__.__name__, id(self))

    def attach(self, transceiver, buffer_size):
        fd = transceiver.socket.fileno()
        buffer = bytearray(buffer_size or default_buffer_size)
        view = memoryview(buffer)
        self.clients[fd] = (transceiver, buffer, view)
        log.debug("Attached %r (buffer_size=%d) to %r", transceiver, buffer_size, self)

    def run(self):
        # TODO: select-based default receiver
        raise NotImplementedError("No receiver implementation is available for this platform")

    @synchronized
    def stop(self):
        if not self._stopped:
            log.debug("Stopping %r", self)
            self._stopped = True

    def stopped(self):
        return self._stopped


class BaseTransceiver(object):
    """ A Transceiver represents a two-way conversation by blending a
    Transmitter with a Receiver.
    """

    Tx = BaseTransmitter
    Rx = BaseReceiver

    transmitter = None
    receiver = None

    @staticmethod
    def new_socket(address):
        socket = _socket(AF_INET, SOCK_STREAM)
        socket.connect(address)
        socket.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
        socket.setblocking(0)
        return socket

    def __init__(self, address, receiver=None, rx_buffer_size=None, *args, **kwargs):
        self.socket = self.new_socket(address)
        self.fd = self.socket.fileno()
        log.info("X[%d]: Connected to %s", self.fd, address)
        self.transmitter = self.Tx(self.socket, *args, **kwargs)
        if receiver:
            self.receiver = receiver
        else:
            self.receiver = self.Rx()
            self.receiver.stopped = lambda: self.stopped()
            self.receiver.start()
        self.receiver.attach(self, rx_buffer_size)

    def __del__(self):
        self.close()

    def __repr__(self):
        return "<%s #%d>" % (self.__class__.__name__, self.fd)

    def transmit(self, *data):
        self.transmitter.transmit(*data)

    def stopped(self):
        return not self.transmitter and not self.receiver

    @synchronized
    def stop_tx(self):
        if self.transmitter:
            log.info("T[%d]: STOP", self.fd)
            try:
                self.socket.shutdown(SHUT_WR)
            except socket_error as error:
                if error.errno not in (EBADF, ENOTCONN):
                    log.error("T[%d]: %s", self.fd, error)
            finally:
                self.transmitter = None
                if self.stopped() and not self.close.locked():
                    self.close()

    @synchronized
    def stop_rx(self):
        if self.receiver:
            try:
                self.on_stop()
            finally:
                log.info("R[%d]: STOP", self.fd)
                try:
                    self.socket.shutdown(SHUT_RD)
                except socket_error as error:
                    if error.errno not in (EBADF, ENOTCONN):
                        log.error("R[%d]: %s", self.fd, error)
                finally:
                    self.receiver = None
                    if self.stopped() and not self.close.locked():
                        self.close()

    @synchronized
    def close(self):
        if self.socket:
            if not self.stop_tx.locked():
                self.stop_tx()
            if not self.stop_rx.locked():
                self.stop_rx()
            try:
                self.socket.close()
            except socket_error:
                pass
            finally:
                self.socket = None
                log.info("X[%d]: Closed", self.fd)

    def on_receive(self, view):
        pass

    def on_stop(self):
        pass
