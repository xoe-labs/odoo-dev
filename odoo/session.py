# -*- coding: utf-8 -*-
import json
import logging

from werkzeug.contrib.sessions import SessionStore
import odoo

_logger = logging.getLogger(__name__)

"""
Title: Odoo-clopud Platform
Autor: Camptocamp
Date: 2018
Code Version: 12
Availability: https://github.com/camptocamp/odoo-cloud-platform/blob/12.0/session_redis/http.py
License: https://github.com/camptocamp/odoo-cloud-platform/blob/12.0/LICENSE
"""

# Importing redis and Sentinel
try:
    import redis
    from redis.sentinel import Sentinel
except ImportError:
    redis = None # noqa
    _logger.debug("Cannot import redis")


def _get_redis():
    # Fix circular import module
    from .http import DEFAULT_SESSION_TIMEOUT

    # loading redis config
    sentinel_host = odoo.tools.config.get_misc('redis_config', 'redis_sentinel_host', None)
    sentinel_master_name = odoo.tools.config.get_misc('redis_config', 'redis_sentinel_master_name', None)
    if sentinel_host and not sentinel_master_name:
        raise Exception("redis_sentinel_master_name must be defined when using session_redis")

    sentinel_port = odoo.tools.config.get_misc('redis_config', 'redis_sentinel_port', None)
    host = odoo.tools.config.get_misc('redis_config', 'redis_host', None)
    port = int(odoo.tools.config.get_misc('redis_config', 'redis_port', None))
    password = odoo.tools.config.get_misc('redis_config', 'redis_pass', None)
    redis_instance = redis.Redis(host=host, port=port, password=password)

    if sentinel_host:
        sentinel = Sentinel([(sentinel_host, sentinel_port)], password=password)
        redis_instance = sentinel.master_for(sentinel_master_name)

    expiration = odoo.tools.config.get_misc('redis_config', 'redis_session_expiration', DEFAULT_SESSION_TIMEOUT)
    return redis_instance, expiration


class RedisSessionStore(SessionStore):
    """
    Saves session to redis
    """
    def __init__(self, prefix='', session_class=None):
        super(RedisSessionStore, self).__init__(session_class=session_class)
        self.redis, self.expiration = _get_redis()
        self.prefix = prefix
        self._healthcheck()

    def _healthcheck(self):
        try:
            self.redis.ping()
        except redis.ConnectionError:
            raise redis.ConnectionError('Redis server is not responding')

    def build_key(self, session_id):
        return "{prefix}{session_id}".format(
        prefix=self.prefix, session_id=session_id)

    def save(self, session):
        key = self.build_key(session.sid)

        # allow to set a custom expiration for a session
        # such as a very short one for monitoring requests
        expiration = session.expiration or self.expiration
        if _logger.isEnabledFor(logging.DEBUG):
            if session.uid:
                user_msg = "user '{user}' (id: {uid})".format(
                    user=session.login, uid=session.uid)
            else:
                user_msg = "anonymous user"
            _logger.debug(
                "saving session with key '{key}' and expiration of {expiration}"
                " seconds for {user_msg}".format(
                    key=key, expiration=expiration, user_msg=user_msg))

        data = json.dumps(dict(session)).encode('utf-8')
        if self.redis.set(key, data):
            return self.redis.expire(key, expiration)

    def delete(self, session):
        key = self.build_key(session.sid)
        _logger.debug("deleting session with key {key}".format(key=key))

        return self.redis.delete(key)

    def get(self, session_id):
        """
        Returns a new session class if session id is invalid or
        is not in redis, in otherwise, returns session class
        with session content or without
        """
        if not self.is_valid_key(session_id):
            _logger.debug(
                "session with invalid session_id '{session_id}' has been asked,"
                " returning a new one".format(session_id=session_id))

            return self.new()

        key = self.build_key(session_id)
        saved = self.redis.get(key)
        if not saved:
            _logger.debug("session with non-existent key '%s' has been asked, "
                "returning a new one", key)
            return self.new()
        try:
            data = json.loads(saved.decode("utf-8"))
        except ValueError:
            _logger.debug(
                "session for key '{key}' has been asked but its json "
                "content could not be read, it has been reset".format(key=key))
            data = {}

        return self.session_class(data, session_id, False)

    def list(self):
        """
        returns the list of called keys
        """
        keys = self.redis.keys("{prefix}*".format(prefix=self.prefix))
        _logger.debug("a listing redis keys has been called")

        return [key[len(self.prefix):] for key in keys]
