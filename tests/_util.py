from itertools import chain, repeat
from io import BytesIO
import os
import sys
import types

from invoke.vendor.six import wraps, iteritems

from fabric import Connection
from fabric.main import program as fab_program
from fabric.transfer import Transfer
from mock import patch, Mock, PropertyMock, call, ANY
from spec import eq_, trap, Spec


support_path = os.path.join(
    os.path.abspath(os.path.dirname(__file__)),
    '_support'
)


# TODO: figure out a non shite way to share Invoke's more beefy copy of same.
@trap
def expect(invocation, out, program=None, test=None):
    if program is None:
        program = fab_program
    program.run("fab {0}".format(invocation), exit=False)
    (test or eq_)(sys.stdout.getvalue(), out)


class Command(object):
    """
    Data record specifying params of a command execution to mock/expect.

    :param str cmd:
        Command string to expect. If not given, no expectations about the
        command executed will be set up. Default: ``None``.

    :param bytes out: Data yielded as remote stdout. Default: ``b""``.

    :param bytes err: Data yielded as remote stderr. Default: ``b""``.

    :param int exit: Remote exit code. Default: ``0``.

    :param int waits:
        Number of calls to the channel's ``exit_status_ready`` that should
        return ``False`` before it then returns ``True``. Default: ``0``
        (``exit_status_ready`` will return ``True`` immediately).
    """
    def __init__(self, cmd=None, out=b"", err=b"", in_=None, exit=0, waits=0):
        self.cmd = cmd
        self.out = out
        self.err = err
        self.in_ = in_
        self.exit = exit
        self.waits = waits


class MockChannel(Mock):
    """
    Mock subclass that tracks state for its ``recv(_stderr)?`` methods.

    Turns out abusing function closures inside MockRemote to track this state
    only worked for 1 command per session!
    """
    def __init__(self, *args, **kwargs):
        # TODO: worth accepting strings and doing the BytesIO setup ourselves?
        # Stored privately to avoid any possible collisions ever. shrug.
        object.__setattr__(self, '__stdout', kwargs.pop('stdout'))
        object.__setattr__(self, '__stderr', kwargs.pop('stderr'))
        # Stdin less private so it can be asserted about
        object.__setattr__(self, '_stdin', BytesIO())
        super(MockChannel, self).__init__(*args, **kwargs)

    def _get_child_mock(self, **kwargs):
        # Don't return our own class on sub-mocks.
        return Mock(**kwargs)

    def recv(self, count):
        return object.__getattribute__(self, '__stdout').read(count)

    def recv_stderr(self, count):
        return object.__getattribute__(self, '__stderr').read(count)

    def sendall(self, data):
        return object.__getattribute__(self, '_stdin').write(data)


class Session(object):
    """
    A mock remote session of a single connection and 1 or more command execs.

    Allows quick configuration of expected remote state, and also helps
    generate the necessary test mocks used by `.mock_remote` itself. Only
    useful when handed into `.mock_remote`.

    The parameters ``cmd``, ``out``, ``err``, ``exit`` and ``waits`` are all
    shorthand for the same constructor arguments for a single anonymous
    `.Command`; see `.Command` for details.

    To give fully explicit `.Command` objects, use the ``commands`` parameter.

    :param str user:
    :param str host:
    :param int port:
        Sets up expectations that a connection will be generated to the given
        user, host and/or port. If ``None`` (default), no expectations are
        generated / any value is accepted.

    :param commands:
        Iterable of `.Command` objects, used when mocking nontrivial sessions
        involving >1 command execution per host. Default: ``None``.

        .. note::
            Giving ``cmd``, ``out`` etc alongside explicit ``commands`` is not
            allowed and will result in an error.
    """
    def __init__(
        self,
        host=None,
        user=None,
        port=None,
        commands=None,
        cmd=None,
        out=None,
        in_=None,
        err=None,
        exit=None,
        waits=None
    ):
        # Sanity check
        params = (cmd or out or err or exit or waits)
        if commands and params:
            raise ValueError("You can't give both 'commands' and individual Command parameters!") # noqa
        # Fill in values
        self.host = host
        self.user = user
        self.port = port
        self.commands = commands
        if params:
            # Honestly dunno which is dumber, this or duplicating Command's
            # default kwarg values in this method's signature...sigh
            kwargs = {}
            if cmd is not None:
                kwargs['cmd'] = cmd
            if out is not None:
                kwargs['out'] = out
            if err is not None:
                kwargs['err'] = err
            if in_ is not None:
                kwargs['in_'] = in_
            if exit is not None:
                kwargs['exit'] = exit
            if waits is not None:
                kwargs['waits'] = waits
            self.commands = [Command(**kwargs)]
        if not self.commands:
            self.commands = [Command()]

    def generate_mocks(self):
        """
        Sets up a mock `.SSHClient` and one or more mock `Channel` objects.

        Specifically, the client will expect itself to be connected to
        ``self.host`` (if given), the channels will be associated with the
        client's `.Transport`, and the channels will expect/provide
        command-execution behavior as specified on the `.Command` objects
        supplied to this `.Session`.

        The client is then attached as ``self.client`` and the channels as
        ``self.channels`.

        :returns:
            ``None`` - this is mostly a "deferred setup" method and callers
            will just reference the above attributes (and call more methods) as
            needed.
        """
        client = Mock()
        transport = client.get_transport.return_value # another Mock

        # NOTE: this originally did chain([False], repeat(True)) so that
        # get_transport().active was False initially, then True. However,
        # because we also have to consider when get_transport() comes back None
        # (which it does initially), the case where we get back a non-None
        # transport _and_ it's not active yet, isn't useful to test, and
        # complicates text expectations. So we don't, for now.
        actives = repeat(True)
        # NOTE: setting PropertyMocks on a mock's type() is apparently
        # How It Must Be Done, otherwise it sets the real attr value.
        type(transport).active = PropertyMock(side_effect=actives)

        channels = []
        for command in self.commands:
            # Mock of a Channel instance, not e.g. Channel-the-class.
            # Specifically, one that can track individual state for recv*().
            channel = MockChannel(
                stdout=BytesIO(command.out),
                stderr=BytesIO(command.err),
            )
            channel.recv_exit_status.return_value = command.exit

            # If requested, make exit_status_ready return False the first N
            # times it is called in the wait() loop.
            readies = chain(repeat(False, command.waits), repeat(True))
            channel.exit_status_ready.side_effect = readies

            channels.append(channel)

        # Have our transport yield those channel mocks in order when
        # open_session() is called.
        transport.open_session.side_effect = channels

        self.client = client
        self.channels = channels

    def sanity_check(self):
        # Per-session we expect a single transport get
        transport = self.client.get_transport
        transport.assert_called_once_with()
        # And a single connect to our target host.
        self.client.connect.assert_called_once_with(
            username=self.user or ANY,
            hostname=self.host or ANY,
            port=self.port or ANY
        )

        # Calls to open_session will be 1-per-command but are on transport, not
        # channel, so we can only really inspect how many happened in
        # aggregate. Save a list for later comparison to call_args.
        session_opens = []

        for channel, command in zip(self.channels, self.commands):
            # Expect an open_session for each command exec
            session_opens.append(call())
            # Expect that the channel gets an exec_command
            channel.exec_command.assert_called_with(command.cmd or ANY)
            # Expect written stdin, if given
            if command.in_:
                eq_(channel._stdin.getvalue(), command.in_)

        # Make sure open_session was called expected number of times.
        eq_(transport.return_value.open_session.call_args_list, session_opens)



class MockRemote(object):
    """
    Class representing mocked remote state.

    Set up for start/stop style patching (so it can be used in situations
    requiring setup/teardown semantics); is then wrapped by e.g. `mock_remote`
    to provide decorator, etc style use.
    """
    # TODO: make it easier to assume one session w/ >1 command?
    def __init__(self, cmd=None, out=None, err=None, in_=None, exit=None,
        commands=None, sessions=None, autostart=True):
        """
        Create & start new remote state.

        Multiple ways to instantiate:

        - no args, for basic "don't explode / touch network" stubbing
        - pass Session args directly for a one-off anonymous session
        - pass ``commands`` kwarg with explicit commands (put into an anonymous
          session)
        - pass ``sessions`` kwarg with explicit sessions

        Combining these approaches is not well defined.

        Will automatically call `start` by default; say ``autostart=False`` to
        disable.
        """
        if commands:
            sessions = [Session(commands=commands)]
        elif not sessions:
            if cmd or out or err or exit:
                session = Session(
                    cmd=cmd, out=out, err=err, in_=in_, exit=exit,
                )
            else:
                session = Session()
            sessions = [session]
        self.sessions = sessions
        if autostart:
            self.start()

    def start(self):
        """
        Start patching SSHClient with the stored sessions, returning channels.
        """
        # Patch SSHClient so the sessions' generated mocks can be set as its
        # return values
        self.patcher = patcher = patch('fabric.connection.SSHClient')
        SSHClient = patcher.start()
        # Mock clients, to be inspected afterwards during sanity-checks
        clients = []
        for session in self.sessions:
            session.generate_mocks()
            clients.append(session.client)
        # Each time the mocked SSHClient class is instantiated, it will
        # yield one of our mocked clients (w/ mocked transport & channel)
        # generated above.
        SSHClient.side_effect = clients
        return chain.from_iterable(x.channels for x in self.sessions)

    def stop(self):
        """
        Stop patching SSHClient, and invoke post-run sanity tests.
        """
        # Stop patching SSHClient
        self.patcher.stop()

        for session in self.sessions:
            # Basic sanity tests about transport, channel etc
            session.sanity_check()


def mock_remote(*sessions):
    """
    Mock & expect one or more remote connections & command executions.

    With no parameterization (``@mock_remote``) or empty parameterization
    (``@mock_remote()``) a single default connection+execution is implied, i.e,
    equivalent to ``@mock_remote(Session())``.

    When parameterized, takes `.Session` objects (see warning below about
    ordering).

    .. warning::
        Due to ``SSHClient``'s API, we must expect connections in the order
        that they are made. If you run into failures caused by explicitly
        expecting hosts in this manner, **make sure** the order of sessions
        and commands you're giving ``@mock_remote`` matches the order in
        which the code under test is creating new ``SSHClient`` objects!

    The wrapped test function must accept a positional argument for each
    command in ``*sessions``, which are used to hand in the mock channel
    objects that are created (so that the test function may make asserts with
    them).

    .. note::
        The actual logic involved is a flattening of all commands across the
        sessions, to make accessing them within the tests fast and easy. E.g.
        in this test setup::

            @mock_remote(
                Session(Command('whoami'), Command('uname')),
                Session(host='foo', cmd='ls /'),
            )

        you would want to set up the test signature for 3 command channels::

            @mock_remote(...)
            def mytest(self, chan_whoami, chan_uname, chan_ls):
                pass

        Most of the time, however, there is a 1:1 map between session and
        command, making this straightforward.
    """
    # Grab func from sessions arg if called bare
    bare = (
        len(sessions) == 1
        and isinstance(sessions[0], types.FunctionType)
    )
    if bare:
        sessions = list(sessions)
        func = sessions.pop()

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Stand up a MockRemote object from closed-over sessions data.
            # TODO: expose more of what MockRemote can handle re: commands,
            # args etc
            remote = MockRemote(sessions=sessions, autostart=False)
            # Start mocking & get returned channel mocks, adding them to the
            # called test function/method.
            args = list(args)
            args.extend(remote.start())
            # Call that test!
            try:
                f(*args, **kwargs)
            finally:
                # Stop mocking & perform sanity tests
                remote.stop()
        return wrapper
    # Bare decorator, no args
    if bare:
        return decorator(func)
    # Args were given
    else:
        return decorator


class MockSFTP(object):
    """
    Class managing mocked SFTP remote state.

    Used in start/stop fashion in eg doctests, wrapped in the `mock_sftp`
    decorator for regular test use.
    """
    def __init__(self, autostart=True):
        if autostart:
            self.start()

    def start(self):
        # Set up mocks
        self.os_patcher = patch('fabric.transfer.os')
        self.client_patcher = patch('fabric.connection.SSHClient')
        mock_os = self.os_patcher.start()
        Client = self.client_patcher.start()
        sftp = Client.return_value.open_sftp.return_value
        # Handle common filepath massage actions; tests will assume these.
        def fake_abspath(path):
            return '/local/{0}'.format(path)
        mock_os.path.abspath.side_effect = fake_abspath
        sftp.getcwd.return_value = '/remote'
        # Ensure stat st_mode is a real number; Python 2 stat.S_IMODE doesn't
        # appear to care if it's handed a MagicMock, but Python 3's does (?!)
        fake_mode = 0o644 # arbitrary real-ish mode
        sftp.stat.return_value.st_mode = fake_mode
        mock_os.stat.return_value.st_mode = fake_mode
        # Not super clear to me why the 'wraps' functionality in mock isn't
        # working for this :(
        mock_os.path.basename.side_effect = os.path.basename
        # Return the sftp and OS mocks for use by decorator use case.
        return sftp, mock_os

    def stop(self):
        self.os_patcher.stop()
        self.client_patcher.stop()


# TODO: dig harder into spec setup() treatment to figure out why it seems to be
# double-running setup() or having one mock created per nesting level...then we
# won't need this probably.
def mock_sftp(expose_os=False):
    """
    Mock SFTP things, including 'os' & handy ref to SFTPClient instance.

    By default, hands decorated tests a reference to the mocked SFTPClient
    instance and an instantiated Transfer instance, so their signature needs to
    be: ``def xxx(self, sftp, transfer):``.

    If ``expose_os=True``, the mocked ``os`` module is handed in, turning the
    signature to: ``def xxx(self, sftp, transfer, mock_os):``.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(self, **kwargs):
            mock = MockSFTP(autostart=False)
            sftp, mock_os = mock.start()
            transfer = Transfer(Connection('host'))
            passed_args = [self, sftp, transfer]
            if expose_os:
                passed_args.append(mock_os)
            return f(*passed_args)
        return wrapper
    return decorator


# TODO: mostly copied from invoke's suite; unify sometime
support = os.path.join(os.path.dirname(__file__), '_support')

class IntegrationSpec(Spec):
    def setup(self):
        # Preserve environment for later restore
        self.old_environ = os.environ.copy()

    def teardown(self):
        # Nuke changes to environ
        os.environ.clear()
        os.environ.update(self.old_environ)
        # Strip any test-support task collections from sys.modules to prevent
        # state bleed between tests; otherwise tests can incorrectly pass
        # despite not explicitly loading/cd'ing to get the tasks they call
        # loaded.
        for name, module in iteritems(sys.modules.copy()):
            if module and support in getattr(module, '__file__', ''):
                del sys.modules[name]
