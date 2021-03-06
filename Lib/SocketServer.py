"""Generic socket server classes.

This module tries to capture the various aspects of defining a server:

For socket-based servers:

- address family:
        - AF_INET{,6}: IP (Internet Protocol) sockets (default)
        - AF_UNIX: Unix domain sockets
        - others, e.g. AF_DECNET are conceivable (see <socket.h>
- socket type:
        - SOCK_STREAM (reliable stream, e.g. TCP)
        - SOCK_DGRAM (datagrams, e.g. UDP)

For request-based servers (including socket-based):

- client address verification before further looking at the request
        (This is actually a hook for any processing that needs to look
         at the request before anything else, e.g. logging)
- how to handle multiple requests:
        - synchronous (one request is handled at a time)
        - forking (each request is handled by a new process)
        - threading (each request is handled by a new thread)

The classes in this module favor the server type that is simplest to
write: a synchronous TCP/IP server.  This is bad class design, but
save some typing.  (There's also the issue that a deep class hierarchy
slows down method lookups.)

There are five classes in an inheritance diagram, four of which represent
synchronous servers of four types:

        +------------+
        | BaseServer |  ## sync servers 
        +------------+
              |
              v
        +-----------+        +------------------+
        | TCPServer |------->| UnixStreamServer |  ## IP vs. Unix system 
        +-----------+        +------------------+
              |
              v
        +-----------+        +--------------------+
        | UDPServer |------->| UnixDatagramServer |
        +-----------+        +--------------------+

Note that UnixDatagramServer derives from UDPServer, not from
UnixStreamServer -- the only difference between an IP and a Unix
stream server is the address family, which is simply repeated in both
unix server classes.

Forking and threading versions of each type of server can be created
using the ForkingServer and ThreadingServer mix-in classes.  For  ## uses of mix-in classes
instance, a threading UDP server class is created as follows:

        class ThreadingUDPServer(ThreadingMixIn, UDPServer): pass

The Mix-in class must come first, since it overrides a method defined
in UDPServer!
## multi-inheritance, right-to-left resolving direction 

## Service 
To implement a service, you must derive a class from
BaseRequestHandler and redefine its handle() method.  You can then run
various versions of the service by combining one of the server classes
with your request handler class.

The request handler class must be different for datagram or stream
services.  This can be hidden by using the mix-in request handler 
classes StreamRequestHandler or DatagramRequestHandler.  ## uses of mix-in classes

Of course, you still have to use your head!

## Process vs. Threads 
For instance, it makes no sense to use a forking server if the service
contains state in memory that can be modified by requests (since the
modifications in the child process would never reach the initial state
kept in the parent process and passed to each child).  In this case,
you can use a threading server, but you will probably have to use
locks to avoid two requests that come in nearly simultaneous to apply
conflicting changes to the server state.

## File system access, sync causes "deaf"
On the other hand, if you are building e.g. an HTTP server, where all
data is stored externally (e.g. in the file system), a synchronous
class will essentially render the service "deaf" while one request is
being handled -- which may be for a very long time if a client is slow
to read all the data it has requested.  Here a threading or forking
server is appropriate.

## Sync server with explicit fork 
In some cases, it may be appropriate to process part of a request
synchronously, but to finish processing in a forked child depending on
the request data.  This can be implemented by using a synchronous
server and doing an explicit fork in the request handler class
handle() method.

## Explicit table, for streaming services 
Another approach to handling multiple simultaneous requests in an
environment that supports neither threads nor fork (or where these are
too expensive or inappropriate for the service) is to maintain an
explicit table of partially finished requests and to use select() to
decide which request to work on next (or whether to handle a new
incoming request).  This is particularly important for stream services
where each client can potentially be connected for a long time (if
threads or subprocesses cannot be used).

Future work:
- Standard classes for Sun RPC (which uses either UDP or TCP)
- Standard mix-in classes to implement various authentication
  and encryption schemes
- Standard framework for select-based multiplexing

XXX Open problems:
- What to do with out-of-band data?

BaseServer:
- split generic "request" functionality out into BaseServer class.
  Copyright (C) 2000  Luke Kenneth Casson Leighton <lkcl@samba.org>

  example: read entries from a SQL database (requires overriding
  get_request() to return a table entry from the database).
  entry is processed by a RequestHandlerClass.

"""

# Author of the BaseServer patch: Luke Kenneth Casson Leighton

# XXX Warning!
# There is a test suite for this module, but it cannot be run by the
# standard regression test.
# To run it manually, run Lib/test/test_socketserver.py.

## Most recently, the file name is changed to lower case: SocketServer.py -> socketserver.py 
__version__ = "0.4"


import socket
import sys
import os

## __all__ is normally seen in __init__.py 
## __all__ is a list of strings defining what symbols in a module will be exported when from <module> import * is used on the module.
## __all__ is a list of public objects of that module -- it overrides the default of hiding everything that begins with an underscore
__all__ = ["TCPServer","UDPServer","ForkingUDPServer","ForkingTCPServer",
           "ThreadingUDPServer","ThreadingTCPServer","BaseRequestHandler",
           "StreamRequestHandler","DatagramRequestHandler",
           "ThreadingMixIn", "ForkingMixIn"]
if hasattr(socket, "AF_UNIX"):
    __all__.extend(["UnixStreamServer","UnixDatagramServer",
                    "ThreadingUnixStreamServer",
                    "ThreadingUnixDatagramServer"])

class BaseServer:

    """Base class for server classes.

    Methods for the caller:

    - __init__(server_address, RequestHandlerClass)
    - serve_forever()
    - handle_request()  # if you do not use serve_forever()
    - fileno() -> int   # for select()

    Methods that may be overridden:

    - server_bind()
    - server_activate()
    - get_request() -> request, client_address
    - verify_request(request, client_address)
    - server_close()
    - process_request(request, client_address)
    - close_request(request)
    - handle_error()

    Methods for derived classes:

    - finish_request(request, client_address)

    Class variables that may be overridden by derived classes or
    instances:

    - address_family
    - socket_type
    - reuse_address

    Instance variables:

    - RequestHandlerClass
    - socket

    """

    def __init__(self, server_address, RequestHandlerClass): 
        """Constructor.  May be extended, do not override."""
        self.server_address = server_address
        self.RequestHandlerClass = RequestHandlerClass  ## Passing a class

    def server_activate(self):
        """Called by constructor to activate the server. 

        May be overridden.

        """
        ## But it is not called by constructor in base class. 
        pass

    def serve_forever(self):
        """Handle one request at a time until doomsday."""
        while 1:  ## forever loop, but while True preferred as in newer version 
            self.handle_request()

    ## 1) Handling, 2) Getting, 3) Processing, 4) Finishing
    # The distinction between handling, getting, processing and
    # finishing a request is fairly arbitrary.  Remember:
    #
    # - handle_request() is the top-level call.  It calls
    #   get_request(), verify_request() and process_request()
    # - get_request() is different for stream or datagram sockets
    # - process_request() is the place that may fork a new process
    #   or create a new thread to finish the request
    # - finish_request() instantiates the request handler class;
    #   this constructor will handle the request all by itself

    ## How should should write a documentation middle in a class

    def handle_request(self):
        """Handle one request, possibly blocking."""
        try:
            request, client_address = self.get_request()
        except socket.error:
            return
        if self.verify_request(request, client_address):
            try:
                self.process_request(request, client_address)  ## when to close? called inside 
            except:
                self.handle_error(request, client_address)
                self.close_request(request)

    def verify_request(self, request, client_address):  ## authentication etc. 
        """Verify the request.  May be overridden.

        Return true if we should proceed with this request.

        """
        return 1

    def process_request(self, request, client_address):
        """Call finish_request.

        Overridden by ForkingMixIn and ThreadingMixIn.

        ## `
        ThreadingMixIn add a new method process_request_thread with exception handling
        Then it overrides this method within initiating of a new thread 

        """
        self.finish_request(request, client_address)
        self.close_request(request)

    def server_close(self):
        """Called to clean-up the server.

        May be overridden.

        """
        pass

    def finish_request(self, request, client_address):
        """Finish one request by instantiating RequestHandlerClass."""
        self.RequestHandlerClass(request, client_address, self)  ## Server --> Handler 

    def close_request(self, request):
        """Called to clean up an individual request."""
        pass

    def handle_error(self, request, client_address):
        """Handle an error gracefully.  May be overridden.

        The default is to print a traceback and continue.

        """
        print '-'*40  ## How you should do the formatting. 
        print 'Exception happened during processing of request from',
        print client_address
        import traceback
        traceback.print_exc() # XXX But this goes to stderr!
        print '-'*40


class TCPServer(BaseServer):

    """Base class for various socket-based server classes.

    Defaults to synchronous IP stream (i.e., TCP).  ## originally, TCP is sync 

    Methods for the caller:

    - __init__(server_address, RequestHandlerClass)
    - serve_forever()
    - handle_request()  # if you don't use serve_forever()
    - fileno() -> int   # for select()

    Methods that may be overridden:

    - server_bind()
    - server_activate()
    - get_request() -> request, client_address
    - verify_request(request, client_address)
    - process_request(request, client_address)
    - close_request(request)
    - handle_error()

    Methods for derived classes:

    - finish_request(request, client_address)

    Class variables that may be overridden by derived classes or
    instances:

    - address_family
    - socket_type
    - request_queue_size (only for stream sockets)
    - reuse_address

    Instance variables:

    - server_address
    - RequestHandlerClass
    - socket

    """

    ## Class variable 
    address_family = socket.AF_INET  ## AF_INET: address format is host and port number
    ## AF_UNIX: AF_UNIX: address format is UNIX pathname

    socket_type = socket.SOCK_STREAM

    request_queue_size = 5

    allow_reuse_address = 0
    
    def __init__(self, server_address, RequestHandlerClass):
        """Constructor.  May be extended, do not override."""
        BaseServer.__init__(self, server_address, RequestHandlerClass)  ## explicitly ref base calss 
        self.socket = socket.socket(self.address_family,
                                    self.socket_type)
        self.server_bind()  ## no params, using object state   ## bind socket to server 
        self.server_activate()  # socket listens 
        
    def server_bind(self):
        """Called by constructor to bind the socket.

        May be overridden.

        """
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)

    def server_activate(self):
        """Called by constructor to activate the server.

        May be overridden.

        """
        self.socket.listen(self.request_queue_size)

    def server_close(self):
        """Called to clean-up the server.

        May be overridden.

        """
        self.socket.close()

    def fileno(self):
        """Return socket file number.

        Interface required by select().
        
        """
        return self.socket.fileno()  ## Delegation, rather than directly use object attributes 

    def get_request(self):
        """Get the request and client address from the socket.

        May be overridden.

        """
        return self.socket.accept()  ## Delegation, rather than directly use object attributes 

    def close_request(self, request):
        """Called to clean up an individual request."""
        request.close()  ## delegation, rather than directly use parameters, since different behaviors 


class UDPServer(TCPServer):

    """UDP server class.
    
    UDP uses a simple connectionless transmission model with a minimum of protocol mechanism. It has no
    handshaking dialogues, and thus exposes any unreliability of the underlying network protocol to the user's
    program. There is no guarantee of delivery, ordering, or duplicate protection.

    Applications use datagram sockets to establish host-to-host communications.

    Handling, Getting Verifying, Processing, Finishing methods are in BaseServer 
    """

    allow_reuse_address = 0

    socket_type = socket.SOCK_DGRAM

    max_packet_size = 8192

    def get_request(self):
        ## UDPServer relying on underlying socket 
        data, client_addr = self.socket.recvfrom(self.max_packet_size)  ## Request from client. 
        return (data, self.socket), client_addr

    def server_activate(self):
        # No need to call listen() for UDP.  ## Server no listening 
        pass

    def close_request(self, request):
        # No need to close anything.
        pass

class ForkingMixIn:

    """Mix-in class to handle each request in a new process.
    Fork to another process with new pid 
    """

    active_children = None  ## list 
    max_children = 40

    def collect_children(self):
        """Internal routine to wait for died children."""
        while self.active_children:
            if len(self.active_children) < self.max_children:
                options = os.WNOHANG  ## This flag specifies that waitpid should return immediately instead of waiting, if there is no child process ready to be noticed. (No Hang)
            else:
                # If the maximum number of children are already
                # running, block while waiting for a child to exit
                options = 0
            try:
                pid, status = os.waitpid(0, options)
            except os.error:
                pid = None
            if not pid: break
            self.active_children.remove(pid)

    def process_request(self, request, client_address):
        """Fork a new subprocess to process the request."""
        self.collect_children()
        pid = os.fork()
        if pid:
            # Parent process
            if self.active_children is None:
                self.active_children = []
            self.active_children.append(pid)
            self.close_request(request)  ## In mix-in, this method relies on child class 
            return
        else:  ## fail elegantly 
            # Child process.
            # This must never return, hence os._exit()!
            try:
                self.finish_request(request, client_address)
                os._exit(0)
            except:
                try:
                    self.handle_error(request, client_address)
                finally:
                    os._exit(1)


class ThreadingMixIn:
    """Mix-in class to handle each request in a new thread."""

    def process_request_thread(self, request, client_address):
        """Same as in BaseServer but as a thread.

        In addition, exception handling is done here.

        Exception handling is added by addining additional method 
        """
        try:
            self.finish_request(request, client_address)
            self.close_request(request)
        except:
            self.handle_error(request, client_address)
            self.close_request(request)

    def process_request(self, request, client_address):
        """Start a new thread to process the request."""
        import threading
        t = threading.Thread(target = self.process_request_thread,
                             args = (request, client_address))
        t.start()


class ForkingUDPServer(ForkingMixIn, UDPServer): pass
class ForkingTCPServer(ForkingMixIn, TCPServer): pass

class ThreadingUDPServer(ThreadingMixIn, UDPServer): pass
class ThreadingTCPServer(ThreadingMixIn, TCPServer): pass

if hasattr(socket, 'AF_UNIX'):

    class UnixStreamServer(TCPServer):
        address_family = socket.AF_UNIX  ## override the address_family 

    class UnixDatagramServer(UDPServer):
        address_family = socket.AF_UNIX

    class ThreadingUnixStreamServer(ThreadingMixIn, UnixStreamServer): pass

    class ThreadingUnixDatagramServer(ThreadingMixIn, UnixDatagramServer): pass

class BaseRequestHandler:

    """Base class for request handler classes.  ## so that you can have HttpResponse 

    This class is instantiated for each request to be handled.  The
    constructor sets the instance variables request, client_address
    and server, and then calls the handle() method.  To implement a
    specific service, all you need to do is to derive a class which
    defines a handle() method.

    ## Server has a reference to Hanlder class. Handler has an attribute of server 

    The handle() method can find the request as self.request, the
    client address as self.client_address, and the server (in case it
    needs access to per-server information) as self.server.  Since a
    separate instance is created for each request, the handle() method
    can define arbitrary other instance variariables.

    """

    def __init__(self, request, client_address, server):
        self.request = request
        self.client_address = client_address
        self.server = server
        try:
            self.setup()
            self.handle()
            self.finish()
        finally:
            sys.exc_traceback = None    # Help garbage collection

    def setup(self):  ## specify what to be overriden. 
        pass

    def handle(self):
        pass

    def finish(self):
        pass


# The following two classes make it possible to use the same service
# class for stream or datagram servers.
# Each class sets up these instance variables:
# - rfile: a file object from which receives the request is read
# - wfile: a file object to which the reply is written
# When the handle() method returns, wfile is flushed properly


class StreamRequestHandler(BaseRequestHandler):

    """Define self.rfile and self.wfile for stream sockets."""

    # Default buffer sizes for rfile, wfile.
    ## Big Read and Big Write - buffer the read, 
    # We default rfile to buffered because otherwise it could be
    # really slow for large data (a getc() call per byte); we make
    # wfile unbuffered because (a) often after a write() we want to
    # read and we need to flush the line; (b) big writes to unbuffered
    # files are typically optimized by stdio even when big reads
    # aren't.

    rbufsize = -1
    wbufsize = 0

    def setup(self):
        self.connection = self.request
        self.rfile = self.connection.makefile('rb', self.rbufsize)  ## b for binary 
        self.wfile = self.connection.makefile('wb', self.wbufsize)

    ## Where to read and writh the files? Maybe need to override handle() 
    def finish(self):
        self.wfile.flush()
        self.wfile.close()
        self.rfile.close()


class DatagramRequestHandler(BaseRequestHandler):

    # XXX Regrettably, I cannot get this working on Linux;
    # s.recvfrom() doesn't return a meaningful client address.

    """Define self.rfile and self.wfile for datagram sockets."""

    def setup(self):
        import StringIO  ## StringIO rather than file 
        self.packet, self.socket = self.request
        self.rfile = StringIO.StringIO(self.packet)
        self.wfile = StringIO.StringIO(self.packet)

    def finish(self):
        self.socket.sendto(self.wfile.getvalue(), self.client_address)
