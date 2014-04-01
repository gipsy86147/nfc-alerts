from google.appengine.ext import db
import timezone
import logging
from wtforms.validators import ValidationError
from wtforms.validators import Length  # (min=-1, max=-1, message=None)
import json

EDT = timezone.Eastern
PDT = timezone.Pacific
CDT = timezone.Central

TZ_MAP = {'EDT': EDT,
          'PDT': PDT,
          'CDT': CDT}

from wtforms.ext.appengine.db import model_form
from wtforms.fields import SelectField, TextAreaField

hours_list = map(lambda x: ("%s" % x, "%s:00" % x), range(24))
tz_list = [('EDT', 'Eastern'), ('PDT', 'Pacific'), ('CDT', 'Central')]


class Settings(db.Model):
    account_sid = db.StringProperty(required=True)
    auth_code = db.StringProperty(required=True)
    twilio_number = db.StringProperty(required=True)
    tz = db.StringProperty()
    alert_enabled = db.BooleanProperty()
    sms_timeout = db.IntegerProperty()


def get_app_tz():
    settings = Settings.all().get()
    logging.info("Time zone: %s" % settings.tz)
    return TZ_MAP.get(settings.tz, EDT) if settings else EDT


class Notifier(db.Model):
    name = db.StringProperty(required=True)
    alert_email = db.EmailProperty()
    from_email = db.EmailProperty(required=True)
    to_phone_list = db.TextProperty()
    time_start = db.IntegerProperty()
    time_end = db.IntegerProperty()
    subject_pattern = db.StringProperty()
    phone_alert_enabled = db.BooleanProperty()
    sms_enabled = db.BooleanProperty()
    phone_custom_message = db.TextProperty()
    sms_message = db.TextProperty()
    readout_email_body = db.BooleanProperty()
    created = db.DateTimeProperty(auto_now_add=True)


class Recipient(db.Model):
    phone_number = db.PhoneNumberProperty(required=True)
    phone_type = db.StringProperty()
    name = db.StringProperty()
    email = db.EmailProperty()
    notifier = db.ReferenceProperty(Notifier, collection_name='recipients')
    created = db.DateTimeProperty(auto_now_add=True)


class NoAlertWindow(db.Model):
    notifier = db.ReferenceProperty(Notifier, collection_name='noAlertWindows')
    start = db.IntegerProperty()
    end = db.IntegerProperty()
    created = db.DateTimeProperty(auto_now_add=True)


class CallQueue(db.Model):
    status = db.StringProperty()
    email_body = db.TextProperty()
    notifier = db.ReferenceProperty(Notifier, collection_name='alerts')
    accepted_by = db.ReferenceProperty(Recipient)
    notification_type = db.StringProperty()
    loop_count = db.IntegerProperty()
    created = db.DateTimeProperty(auto_now_add=True)

    def get_traces_json(self):
        traces = []
        for trace in self.traces.order('-created'):
            traces.append({'created': trace.created_in_app_tz().isoformat(), 'event_log': trace.event_log})
        return json.dumps(traces)


class CallQueueEntry(db.Model):
    phone_number = db.StringProperty()
    sequence = db.IntegerProperty()
    status = db.StringProperty()
    entry_type = db.StringProperty()
    call_queue = db.ReferenceProperty(CallQueue, collection_name='entries')
    recipient = db.ReferenceProperty(Recipient)
    created = db.DateTimeProperty(auto_now_add=True)


class AlertTrace(db.Model):
    event_log = db.TextProperty()
    created = db.DateTimeProperty(auto_now_add=True)
    call_queue = db.ReferenceProperty(CallQueue, collection_name='traces')

    def created_in_app_tz(self):
        tz = get_app_tz()
        utc = timezone.UTC()
        return self.created.replace(tzinfo=utc).astimezone(tz)


class NotifierForm(model_form(Notifier, exclude=('alert_email', 'created'))):
    time_start = SelectField('Alert start', choices=hours_list)
    time_end = SelectField('Alert end', choices=hours_list)
    sms_message = TextAreaField('SMS Message', validators=[Length(min=-1, max=140)])


class RecipientForm(model_form(Recipient, exclude=('notifier', 'created'))):
    pass


class SettingsForm(model_form(Settings)):
    tz = SelectField('Time zone', choices=tz_list)

    
    
    