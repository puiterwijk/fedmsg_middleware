try:
    from urllib2 import urlopen
except ImportError:
    from urllib import urlopen

import BeautifulSoup
import webob
import json

import fedmsg.config
import fedmsg.text as t

from moksha.common.lib.helpers import get_moksha_appconfig
from moksha.wsgi.widgets.api import get_moksha_socket
from moksha.wsgi.widgets.api import LiveWidget
from tw2.jqplugins.gritter import gritter_resources

configuration_base_url = 'http://localhost:6543/prefs/'

truthy = frozenset(('t', 'true', 'y', 'yes', 'on', '1'))

class PersonalConfig:
    enabled = True
    timeout = 10000
    enabled_filters = ['announce']
    usernames = []
    maintainers = []
    packages = []

class FedmsgMiddleware(object):
    """ WSGI middleware that injects a moksha socket for fedmsg popups """

    def get_personal_preferences(username):
        if not username:
            return PersonalConfig()
        req = urlopen('%(base)s%(user)s' % {'base': configuration_base_url, 'user': username})
        if not req:
            return PersonalConfig()
        data = req.read()
        if not data:
            return PersonalConfig()
        result = json.loads(data)
        if not result:
            return PersonalConfig()
        if result['status'] != 'OK':
            return PersonalConfig()
        cfg = PersonalConfig()
        cfg.enabled = result['enabled']
        cfg.timeout = result['timeout']
        cfg.enabled_filters = result['enabled_filters']
        cfg.usernames = result['usernames']
        cfg.maintainers = result['maintainers']
        cfg.packages = result['packages']
        return cfg


    def __init__(self, app, config=None, get_username_function=None):
        """ Configuration arguments are documented in README.rst """

        self.app = app
        self.config = config
        self.preferences = get_personal_preferences(get_username_function())

        if not self.config:
            self.config = get_moksha_appconfig()

        # Initialize fedmsg its own config in /etc/fedmsg.d/
        self.fedmsg_config = fedmsg.config.load_config(None, [])
        t.make_processors(**self.fedmsg_config)

    def __call__(self, environ, start_response):
        """ Process a request. """

        req = webob.Request(environ)

        if self.should_respond(req):
            # Is this an ajax request asking for fedmsg.text information?
            # If so.. forget everything else and just handle that before anyone
            # else notices ;o
            resp = self.serve_response(req)
            return resp(environ, start_response)

        # Register our fancy widget with the moksha middleware.
        PopupNotification.display()

        # Pass the request on to the app that we wrap.
        resp = req.get_response(self.app, catch_exc_info=True)

        # Should we modify their response and inject the moksha_socket?
        # We'll do so in a hopefully delicate manner that
        # doesn't disturb other javascript (like jQuery).
        if self.should_inject(req, resp):
            resp = self.inject(resp)

        return resp(environ, start_response)

    def should_respond(self, req):
        """ Determine if this is an AJAX request for fedmsg.text metadata """
        return req.environ['PATH_INFO'] == "/__fedmsg.text__"

    def serve_response(self, req):
        """ Translate a fedmsg message into metadata for gritter. """
        msg = json.loads(req.GET['json'])

        proc = t.msg2processor(msg)
        text = """<a href="{link}" target="_blank">{text}</a>""".format(
            text=t.msg2subtitle(msg, proc, **self.fedmsg_config),
            link=t.msg2link(msg, proc, **self.fedmsg_config),
        )
        metadata = dict(
            title="fedmsg -> " + t.msg2title(msg, proc, **self.fedmsg_config),
            text=text,
            image=t.msg2secondary_icon(msg, proc, **self.fedmsg_config),
            time=10000,  # 10 seconds until messages disappear
        )

        resp = webob.Response(
            content_type='application/json',
            body=json.dumps(metadata),
        )
        return resp

    def should_inject(self, req, resp):
        """ Determine if this request should be modified.  Boolean. """

        if resp.status != "200 OK":
            return False

        content_type = resp.headers.get('Content-Type', 'text/plain').lower()
        if not 'html' in content_type:
            return False

        # Did the wrapped app already inject a moksha socket?
        if 'moksha_websocket = ' in resp.body:
            return False

        return True

    def inject(self, resp):
        """ Inject notification machinery into this response!

        Insert javascript into the <head> tag.
        """

        soup = BeautifulSoup.BeautifulSoup(resp.body)

        if not soup.html:
            return resp

        if not soup.html.head:
            soup.html.insert(0, BeautifulSoup.Tag(soup, "head"))

        def add_payload(payload):
            payload = BeautifulSoup.BeautifulSoup(payload)
            soup.html.body.insert(len(soup.html.body), payload)

        socket = get_moksha_socket(self.config)

        add_payload(PopupNotification.display())
        add_payload(socket().display())

        resp.body = str(soup.prettify())
        return resp


class PopupNotification(LiveWidget):
    topic = "*"
    onmessage = """
    (function(json){
        // Make an ajax request to get the fedmsg.text metadata and use that
        // metadata to make the gritter popup.
        $.ajax("/__fedmsg.text__", {
                data: {json: JSON.stringify(json)},
                success: $.gritter.add,
        });
    })(json);
    """
    resources = LiveWidget.resources + gritter_resources
    backend = "websocket"

    # Don't actually produce anything when you call .display() on this widget.
    inline_engine_name = "mako"
    template = ""


def make_middleware(app=None, *args, **kw):
    app = FedmsgMiddleware(app, *args, **kw)
    return app
