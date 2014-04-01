import webapp2 as webapp
from webapp2_extras import jinja2
from webapp2_extras import sessions
from google.appengine.ext.db import users
from datetime import  datetime, timedelta
from models import get_app_tz
import timezone
import logging
from models import Settings
config = {}
config['webapp2_extras.sessions'] = {
    'secret_key': 'xjhfjsdhfjshdjfh@#$#%$#%^@$@#%$cvnhvkdjgkHJFKJshfhdfjk',
}
class BaseHandler(webapp.RequestHandler):
    def dispatch(self):
        # Get a session store for this request.
        self.session_store = sessions.get_store(request=self.request)

        try:
            # Dispatch the request.
            webapp.RequestHandler.dispatch(self)
        finally:
            # Save all sessions.
            self.session_store.save_sessions(self.response)

    @webapp.cached_property
    def session(self):
        # Returns a session using the default cookie key.
        return self.session_store.get_session()
    
    @webapp.cached_property
    def jinja2(self):
        # Returns a Jinja2 renderer cached in the app registry.
        return jinja2.get_jinja2(app=self.app)
        

    def render_response(self, _template, **context):
        settings = Settings.all().get()
        # Renders a template and writes the result to the response.
        logout = '/logout'
        email = users.get_current_user().email()
        context['admin'] = users.is_current_user_admin()
        context['session'] = self.session
        context['request'] = self.request
        context['logout'] = logout
        context['email'] = email
        context['settings'] = settings
        rv = self.jinja2.render_template(_template, **context)
        self.response.write(rv)






def is_in_alert_period1(start,end,current_hour=None):
    if not current_hour:
        apptz = get_app_tz()
        now = datetime.now(apptz)
        current_hour = now.hour
        
    result = False
    if start > end:
        result = current_hour >= start or current_hour < end
        logging.info('start > end : Result:%s'%result)
    else:
        result = current_hour >= start and current_hour < end
        logging.info('start < end : Result:%s'%result)
    logging.info('start: %s end: %s current_hour: %s is_in_alert_period: %s'%(start, end, current_hour, result))
    return result

def is_notifier_in_alert_period(notif, check_time=None):
    if not is_in_alert_period1(notif.time_start, notif.time_end, check_time):
        return False
    for noalertWindow in notif.noAlertWindows:
        if is_in_alert_period1(noalertWindow.start, noalertWindow.end, check_time):
            return False
    return True

def clone_entity(e, **extra_args):
    """Clones an entity, adding or overriding constructor attributes.
    
    The cloned entity will have exactly the same property values as the original
    entity, except where overridden. By default it will have no parent entity or
    key name, unless supplied.
    
    Args:
      e: The entity to clone
      extra_args: Keyword arguments to override from the cloned entity and pass
        to the constructor.
    Returns:
      A cloned, possibly modified, copy of entity e.
    """
    klass = e.__class__
    props = dict((k, v.__get__(e, klass)) for k, v in klass.properties().iteritems())
    props.update(extra_args)
    return klass(**props)

        