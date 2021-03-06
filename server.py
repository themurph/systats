#!/usr/bin/env python
"""
Self contained stats consumer.
"""

import datetime
import os.path
import re

try:
    import json
except ImportError:
    # Fallback for 2.5
    import simplejson as json

import urllib

import wsgiref.util

import logging
import logging.handlers


def create_logger(name, filename,
                  format='%(asctime)s - %(levelname)s - %(message)s'):
    """
    Creates a logger instance.
    """
    logfile = os.path.sep.join([os.path.realpath(
        json.load(open('config.json', 'r'))['logdir']),
        filename])
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not os.path.exists(os.path.dirname(logfile)):
        os.makedirs(os.path.dirname(logfile))
    handler = logging.handlers.TimedRotatingFileHandler(
        logfile, 'd')
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(format))
    logger.addHandler(handler)
    return logger


class Router(object):
    """
    URL Router.
    """

    def __init__(self, rules):
        """
        Creates an application URI router.

        rules is a dictionary defining uri: WSGIApplication
        """
        self._rules = {}
        for uri, app in rules.items():
            self._rules[uri] = {'app': app, 'regex': re.compile(uri)}

    def __call__(self, environ, start_response):
        """
        Callable which handles the actual routing in a WSGI structured way.
        """
        # If the path exists then pass control to the wsgi application
        if environ['PATH_INFO'] in self._rules.keys():
            return self._rules[environ['PATH_INFO']]['app'].__call__(
                environ, start_response)

        # If the path matches the regex then pass control to the wsgi app
        for uri, data in self._rules.items():
            # skip '' because it would always match
            if uri == '':
                continue
            if data['regex'].match(environ['PATH_INFO']):
                kwargs = data['regex'].match(environ['PATH_INFO']).groupdict()
                return data['app'].__call__(environ, start_response, **kwargs)

        # Otherwise 404
        start_response("404 File Not Found", [("Content-Type", "text/html")])
        return "404 File Not Found."


class BaseHandler(object):
    """
    Base handler to be used for app endpoints.
    """
    _conf = json.load(open('config.json', 'r'))
    logger = create_logger('systats', 'systats_app.log')

    def __init__(self):
        """
        Creates a BaseHandler instance.
        """
        self._template_path = os.path.sep.join([os.path.realpath(
            self._conf['templatedir']), 'templates'])
        self._cache_dir = os.path.realpath(self._conf['cachedir'])
        self._cache_time = datetime.timedelta(**self._conf['cachetime'])

    def render_template(self, name, **kwargs):
        """
        Template renderer.
        """
        tpl = open(os.path.sep.join([self._template_path, name]), 'r').read()
        for key, value in kwargs.items():
            tpl = tpl.replace("{{- " + key + " -}}", value)
        return tpl

    def get_from_cache(self, key, source=None):
        """
        Gets data from a local cache. If it's not there it will run the
        source callable, save the result and return the results.
        """
        cache_name = os.path.sep.join([self._cache_dir, key + '.json'])
        if os.path.exists(cache_name):
            mtime = datetime.datetime.fromtimestamp(
                os.stat(cache_name).st_mtime)
            now = datetime.datetime.now()
            # If we are still in the cache time then use the cache
            if now - self._cache_time < mtime:
                self.logger.info('Found "%s" in cache.' % key)
                data = open(cache_name, 'r').read()
                return data
            else:
                self.logger.info('Key "%s" is expired in cache.' % key)

        self.logger.info('Key "%s" was NOT in cache.' % key)
        try:
            data = source()
            self.save_to_cache(key, data)
            return data
        except Exception, ex:
            print ex

    def save_to_cache(self, key, data):
        """
        Holds data in local 'cache'.
        """
        cache_name = os.path.sep.join([self._cache_dir, key + '.json'])
        json_data = json.loads(data)
        f = open(cache_name, 'w')
        f.write(json.dumps(json_data))
        f.close()
        self.logger.info('Saved "%s" in cache.' % key)

    def return_404(self, start_response, msg="404 File Not Found"):
        """
        Shortcut for returning 404's.
        """
        start_response("404 File Not Found", [("Content-Type", "text/html")])
        return str(msg)


class StaticFileHandler(BaseHandler):
    """
    Handles static file serving. Will ONLY serve 1 directory deep!!
    """

    def __call__(self, environ, start_response, filename):
        """
        Returns the content of a CSS, JS or PNG file with the right mimetype
        if it exists.
        """
        real_name = os.path.sep.join(
            [os.path.realpath(self._conf['staticdir']), filename])
        if os.path.exists(real_name) and os.path.isfile(real_name):
            mime_type = 'text/plain'
            if real_name.endswith('.js'):
                mime_type = 'application/javascript'
            elif real_name.endswith('.css'):
                mime_type = 'text/css'
            elif real_name.endswith('.png'):
                mime_type = 'image/png'
            start_response("200 OK", [(
                "Content-Type", mime_type)])
            f = open(real_name, 'r')
            for line in f.readlines():
                yield line
            f.close()
        else:
            yield self.return_404(start_response)


class IndexHandler(BaseHandler):
    """
    Index page.
    """

    def __call__(self, environ, start_response):
        """
        Handles the index page.
        """
        start_response("200 OK", [("Content-Type", "text/html")])
        return self.render_template('base.html', title='Systats')


class ListHostsHandler(BaseHandler):
    """
    Hosts page.
    """

    def __call__(self, environ, start_response):
        """
        Handles the REST API endpoint listing known hosts.
        """
        start_response("200 OK", [("Content-Type", "application/json")])
        return json.dumps(self._conf['hosts'])


class ListEnvsHandler(BaseHandler):
    """
    Envs page.
    """

    def __call__(self, environ, start_response):
        """
        Handles the REST API endpoint listing known environments.
        """
        start_response("200 OK", [("Content-Type", "application/json")])
        return json.dumps(self._conf['hosts'].values())


class QueryHostHandler(BaseHandler):
    """
    Host stats page.
    """

    def __call__(self, environ, start_response, host):
        """
        Handles the REST API proxy between restfulstatsjson and the web ui.
        """
        if host in self._conf['hosts']:
            call_obj = lambda: str(urllib.urlopen(
                self._conf['endpoint'] % host).read())

            data = self.get_from_cache(host, call_obj)
            json_data = json.loads(data)

            start_response("200 OK", [("Content-Type", "application/json")])
            return json.dumps(json_data)

        return self.return_404(start_response)


def make_app():
    """
    Creates a WSGI application for use.
    """
    return Router({
        '^/$': IndexHandler(),
        '/hosts.json$': ListHostsHandler(),
        '/envs.json$': ListEnvsHandler(),
        '/host/(?P<host>[\w\.]*).json?$': QueryHostHandler(),
        '/static/(?P<filename>[\w\-\.]*$)': StaticFileHandler(),
    })


def run_server():
    """
    If the server is called directly then serve via wsgiref.
    """
    # Using optparse since argparse is not available in 2.5
    from optparse import OptionParser

    parser = OptionParser()
    parser.add_option('-p', '--port', dest='port', default=8080,
                      help='Port to listen on. (Default: 8080)')
    parser.add_option(
        '-l', '--listen', dest='listen', default='0.0.0.0',
        help='Address to listen on. (Default: 0.0.0.0)')

    (options, args) = parser.parse_args()

    try:
        from wsgiref.simple_server import make_server, WSGIRequestHandler

        logger = create_logger(
            'systats_access', 'systats_access.log', '%(message)s')

        class SystatsHandler(WSGIRequestHandler):

            def log_message(self, format, *args):
                logger.info("%s - - [%s] %s" % (
                    self.address_string(),
                    self.log_date_time_string(),
                    format % args))

        application = make_app()

        httpd = make_server(
            options.listen, int(options.port), application,
            handler_class=SystatsHandler)
        print "server listening on http://%s:%s" % (
            options.listen, options.port)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print "shutting down..."
        raise SystemExit(0)


if __name__ == "__main__":
    run_server()
