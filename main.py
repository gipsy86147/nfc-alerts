from google.appengine.ext import webapp
from common import BaseHandler, config, is_notifier_in_alert_period
from google.appengine.api import users
from models import Notifier, NotifierForm, SettingsForm, Settings, CallQueue, CallQueueEntry, Recipient, hours_list, \
    NoAlertWindow, AlertTrace
from google.appengine.api import app_identity
from google.appengine.ext.webapp.mail_handlers import InboundMailHandler
import logging
from twilio import twiml
import twilio_tasks
from twilio_tasks import sms_task
from google.appengine.ext import db
import json
from google.appengine.ext import deferred


class MainPage(BaseHandler):
    def get(self):
        self.render_response('index.html')


class LogoutPage(BaseHandler):
    def get(self):
        self.redirect(users.create_logout_url('/'))


class NotifiersPage(BaseHandler):
    def get(self):
        form = NotifierForm(self.request.GET)
        alerts = Notifier.all().order('-created')
        self.render_response('notifiers.html', alerts=alerts, form=form)


class NewOrEditNotifierPage(BaseHandler):
    def get(self):
        notif_id = self.request.get('id')
        form = NotifierForm()
        notif = None
        if notif_id:
            notif = Notifier.get_by_id(int(notif_id))
            form = NotifierForm(obj=notif)
        self.render_response('notifier_form.html', form=form, notif=notif, hours_list=hours_list)

    def post(self):
        notif_id = self.request.get('id')
        form = NotifierForm(self.request.POST)
        notif = None
        if notif_id:
            notif = Notifier.get_by_id(int(notif_id))
            form = NotifierForm(self.request.POST, obj=notif)

        if form.validate():
            if not notif:
                notif = Notifier(name=form.name.data,
                                 from_email=form.from_email.data,
                                 to_phone_list=form.to_phone_list.data,
                                 time_start=int(form.time_start.data),
                                 time_end=int(form.time_end.data),
                                 subject_pattern=form.subject_pattern.data,
                                 phone_alert_enabled=form.phone_alert_enabled.data,
                                 sms_enabled=form.sms_enabled.data,
                                 phone_custom_message=form.phone_custom_message.data,
                                 readout_email_body=form.readout_email_body.data,
                                 sms_message=form.sms_message.data,

                )
            else:
                notif.name = form.name.data
                notif.from_email = form.from_email.data
                notif.to_phone_list = form.to_phone_list.data
                notif.time_start = int(form.time_start.data)
                notif.time_end = int(form.time_end.data)
                notif.subject_pattern = form.subject_pattern.data
                notif.phone_alert_enabled = form.phone_alert_enabled.data
                notif.sms_enabled = form.sms_enabled.data
                notif.phone_custom_message = form.phone_custom_message.data
                notif.readout_email_body = form.readout_email_body.data
                notif.sms_message = form.sms_message.data

            notif.put()
            notif.alert_email = '%s@%s.appspotmail.com' % (notif.key().id(), app_identity.get_application_id())
            notif.save()
            self.redirect('/notifiers')
        else:
            self.render_response('notifier_form.html', form=form, notif=notif)


class SettingsPage(BaseHandler):
    def get(self):
        settings = Settings.all().get()
        form = SettingsForm(self.request.GET, obj=settings)
        self.render_response('settings.html', form=form)

    def post(self):
        settings = Settings.all().get()
        form = SettingsForm(self.request.POST, obj=settings)
        if form.validate():
            if settings:
                settings.auth_code = form.auth_code.data
                settings.account_sid = form.account_sid.data
                settings.twilio_number = form.twilio_number.data
                settings.tz = form.tz.data
                settings.sms_timeout = form.sms_timeout.data
            else:
                settings = Settings(auth_code=form.auth_code.data, account_sid=form.account_sid.data,
                                    twilio_number=form.twilio_number.data, tz=form.tz.data,
                                    sms_timeout=form.sms_timeout.data)
            settings.put()
            self.redirect('/settings')
        else:
            self.render_response('settings.html', form=form, settings=settings)


class CallHandler(BaseHandler):
    def get(self):
        call_queue_id = self.request.get('call_queue_id')
        queue_entry_id = self.request.get('queue_entry_id')
        call_queue = CallQueue.get_by_id(int(call_queue_id))
        queue_entry = CallQueueEntry.get_by_id(int(queue_entry_id))
        r = twiml.Response()
        r.say(call_queue.notifier.phone_custom_message)
        if call_queue.notifier.readout_email_body:
            r.say(call_queue.email_body)
        g = r.gather(action='/call?mode=gather&call_queue_id=%s&queue_entry_id=%s' % (call_queue_id, queue_entry_id),
                     numDigits=1)
        g.say('Press 1 to repeat the message. Press 2 to accept the alert. Press 3 to pass it.')
        self.response.out.write(r)

    def post(self):
        call_queue_id = self.request.get('call_queue_id')
        queue_entry_id = self.request.get('queue_entry_id')
        mode = self.request.get('mode')
        r = twiml.Response()

        call_queue = CallQueue.get_by_id(int(call_queue_id))
        queue_entry = CallQueueEntry.get_by_id(int(queue_entry_id))
        if mode == 'gather':
            digit = self.request.get('Digits')
            if digit == '1':
                r.redirect('/call?call_queue_id=%s&queue_entry_id=%s' % (call_queue_id, queue_entry_id), method='GET')
            elif digit == '2':
                call_queue.status = 'C'
                call_queue.accepted_by = queue_entry.recipient
                call_queue.put()
                event_log = 'Alert accepted by %s(%s)' % (
                    queue_entry.recipient.name, queue_entry.recipient.phone_number)
                alert_trace = AlertTrace(event_log=event_log, call_queue=call_queue)
                alert_trace.put()
                r.say("Thank you.")
                r.hangup()
            else:
                r.say("Thank you.")
                r.hangup()
                event_log = 'Alert passed by %s(%s)' % (queue_entry.recipient.name, queue_entry.recipient.phone_number)
                alert_trace = AlertTrace(event_log=event_log, call_queue=call_queue)
                alert_trace.put()
        self.response.out.write(r)


class SMSHandler(BaseHandler):
    def get(self):
        logging.info('Twilio incoming sms:%s' % self.request.get('From'))
        from_number = self.request.get('From')[-10:]
        logging.info('From phone_number:%s' % from_number)
        body = self.request.get('Body')
        logging.info("Body:%s" % body)
        queue_entry = CallQueueEntry.all().filter('entry_type =',
                                                  'sms').filter('phone_number =', from_number).order('-created').get()
        logging.info("queue_entry:%s" % queue_entry)
        if queue_entry:
            sms_queue = queue_entry.call_queue
            if body == '1':
                sms_queue.status = 'C'
                sms_queue.accepted_by = queue_entry.recipient
                sms_queue.put()
                event_log = 'Alert accepted by %s(%s)' % (
                    queue_entry.recipient.name, queue_entry.recipient.phone_number)
                alert_trace = AlertTrace(event_log=event_log, call_queue=sms_queue)
                alert_trace.put()

    def post(self):
        self.get()


class DeleteHandler(BaseHandler):
    def get(self):
        key = self.request.get('key')
        _next = self.request.get('next', '/notifiers')
        db.delete(key)
        self.redirect(_next)


class CallStatusHandler(BaseHandler):
    def post(self):
        call_queue_id = self.request.get('call_queue_id')
        call_queue = CallQueue.get_by_id(int(call_queue_id))
        if call_queue.status == 'P':
            twilio_tasks.notify_task(int(call_queue_id))


class AlertEnableDesableHandler(BaseHandler):
    def post(self):
        enable_desable = self.request.get('enable_desable')
        settings = Settings.all().get()
        if not settings:
            self.redirect('/settings')
        else:
            if 'enable' == enable_desable:
                settings.alert_enabled = True
            else:
                settings.alert_enabled = False
            settings.put()
        self.redirect('/')


class RecipientsHandler(BaseHandler):
    def get(self, notif_id):
        notif = Notifier.get_by_id(int(notif_id))
        self.render_response('recipients.html', notif=notif)


class AlertsReportHandler(BaseHandler):
    def get(self, notif_id):
        notif = Notifier.get_by_id(int(notif_id))
        self.render_response('alerts.html', notif=notif)


class EmailHandler(InboundMailHandler):
    def receive(self, mail_message):
        logging.info('Incoming email')
        settings = Settings.all().get()
        logging.info('Alerts are enabled:%s' % settings.alert_enabled)
        if settings == None or settings.alert_enabled == False:
            logging.info('Alerts are paused. Ignoring in coming e-mails.')
            return
        plaintext_bodies = mail_message.bodies('text/plain')
        text_bodies = []
        for b in plaintext_bodies:
            payload = b[1]
            text_bodies.append(payload.decode())
        text_body = " ".join(text_bodies)
        logging.info("Mail raw body:%s" % mail_message.body)
        logging.info("Mail body text:%s" % text_body)

        tomail = mail_message.to.replace('"', '').replace("'", '')
        from_mail = mail_message.sender.replace('"', '').replace("'", '')
        if mail_message.sender.find('<') != -1:
            from_mail = from_mail.split('<')[1].split('>')[0]
        if mail_message.to.find('<') != -1:
            tomail = tomail.split('<')[1].split('>')[0]
        subject = ''
        try:
            subject = mail_message.subject
        except:
            logging.info("No Subject")
            pass
        logging.info('To:%s From:%s Subject:%s' % (tomail, from_mail, subject))
        notif = Notifier.all().filter('alert_email =', tomail).get()
        eligible = notif and from_mail.find(notif.from_email) != -1 and (
            notif.subject_pattern == None or notif.subject_pattern == '' or subject.find(
                notif.subject_pattern) != -1) and is_notifier_in_alert_period(notif)
        logging.info("Alert eligible : %s" % eligible)

        if eligible:
            logging.info("Calling create_notify_queue_task")
            twilio_tasks.create_notify_queue_task(notif, email_body=text_body)


# JSON Services   

class RecipientsService(BaseHandler):
    def get(self, notif_id):
        try:
            notif = Notifier.get_by_id(int(notif_id))
            result_dict = {'status': 'success'}
            recipients = []
            for recipient in notif.recipients:
                recipients.append({'phone_number': recipient.phone_number, 'email': recipient.email,
                                   'phone_type': recipient.phone_type,
                                   'name': recipient.name, 'id': recipient.key().id(), 'key': '%s' % recipient.key()})
            result_dict['recipients'] = recipients
            result_dict['notif_id'] = notif_id
        except:
            result_dict = {'status': 'failure'}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(result_dict))

    def post(self, notif_id):
        try:
            result_dict = {'status': 'success'}
            notif = Notifier.get_by_id(int(notif_id))
            phone_number = self.request.get('phone_number')
            name = self.request.get('name')
            email = self.request.get('email')
            phone_type = self.request.get('phoneType')
            recipient_id = self.request.get('recipient_id', '')
            if recipient_id != '':
                recipient = Recipient.get_by_id(int(recipient_id))
                recipient.phone_number = phone_number
                recipient.phone_type = phone_type
                recipient.name = name
                recipient.email = email
                recipient.notifier = notif
            else:
                recipient = Recipient(phone_number=phone_number, name=name, email=email, phone_type=phone_type,
                                      notifier=notif)
            recipient.put()
        except:
            result_dict = {'status': 'failure'}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(result_dict))


class NoAlertWindowsHandler(BaseHandler):
    def get(self, notif_id):
        try:
            result_dict = {'status': 'success'}
            notif = Notifier.get_by_id(int(notif_id))
            noAlertWindows = []
            for noAlertWindow in notif.noAlertWindows:
                noAlertWindows.append(
                    {'key': '%s' % noAlertWindow.key(), 'start': noAlertWindow.start, 'end': noAlertWindow.end})
            result_dict['noAlertWindows'] = noAlertWindows
            result_dict['notif_id'] = notif_id
        except:
            result_dict = {'status': 'failure'}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(result_dict))

    def post(self, notif_id):
        try:
            result_dict = {'status': 'success'}
            notif = Notifier.get_by_id(int(notif_id))
            start = self.request.get('start')
            end = self.request.get('end')
            NoAlertWindow(notifier=notif, start=int(start), end=int(end)).put()
        except:
            result_dict = {'status': 'failure'}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(result_dict))


class AlertTraceHandler(BaseHandler):
    def get(self, queue_id):
        call_queue = CallQueue.get_by_id(int(queue_id))


application = webapp.WSGIApplication([('/', NotifiersPage),
                                      ('/notifiers', NotifiersPage),
                                      ('/notifiers/new', NewOrEditNotifierPage),
                                      ('/notifiers/edit', NewOrEditNotifierPage),
                                      ('/logout', LogoutPage),
                                      ('/settings', SettingsPage),
                                      ('/call', CallHandler),
                                      ('/sms', SMSHandler),
                                      ('/delete', DeleteHandler),
                                      ('/callStatus', CallStatusHandler),
                                      ('/alertEnable', AlertEnableDesableHandler),
                                      ('/notifiers/(\d+)/recipients', RecipientsHandler),
                                      ('/services/notifiers/(\d+)/recipients', RecipientsService),
                                      ('/notifiers/(\d+)/noAlertWindows', NoAlertWindowsHandler),
                                      ('/notifiers/(\d+)/reports', AlertsReportHandler),
                                      ('/_ah/warmup', NotifiersPage),
                                      EmailHandler.mapping()], debug=True, config=config)


# def main():
#    run_wsgi_app(application)
#
#
#if __name__ == "__main__":
#    main()
