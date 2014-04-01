from models import CallQueue, Settings, CallQueueEntry, AlertTrace
from twilio.rest import TwilioRestClient
from google.appengine.api import app_identity
from google.appengine.ext import deferred
import logging
from google.appengine.ext import db
from common import clone_entity
import traceback


def call_task(call_queue_id):
    try:
        settings = Settings.all().get()
        client = TwilioRestClient(settings.account_sid, settings.auth_code)
        call_queue = CallQueue.get_by_id(call_queue_id)
        next_entry = call_queue.entries.filter('status =', 'P').filter('entry_type =', 'phone').order('sequence').get()
        if next_entry and call_queue.status == 'P':
            next_entry.status = 'C'
            next_entry.put()
            client.calls.create(to=next_entry.phone_number, from_=settings.twilio_number,
                                url='http://%s.appspot.com/call?call_queue_id=%s&queue_entry_id=%s' % (
                                    app_identity.get_application_id(), call_queue_id, next_entry.key().id()),
                                status_callback='http://%s.appspot.com/callStatus?call_queue_id=%s&queue_entry_id=%s' % (
                                    app_identity.get_application_id(), call_queue_id, next_entry.key().id()),
                                method='GET', timeout=15)

    except:
        logging.error("call_task failed for  queue_id : %s" % call_queue_id)


def sms_task(queue_id):
    try:
        settings = Settings.all().get()
        client = TwilioRestClient(settings.account_sid, settings.auth_code)
        sms_queue = CallQueue.get_by_id(queue_id)
        next_entry = sms_queue.entries.filter('status =', 'P').filter('entry_type =', 'sms').order('sequence').get()
        if next_entry and sms_queue.status == 'P':
            next_entry.status = 'C'
            next_entry.put()
            sms_message = '%s Send 1 to accept' % sms_queue.notifier.sms_message
            client.sms.messages.create(to=next_entry.phone_number,
                                       from_=settings.twilio_number,
                                       body=sms_message)
            deferred.defer(sms_task, queue_id, _countdown=settings.sms_timeout)
    except:
        logging.error("sms_task failed for  queue_id : %s" % queue_id)


def notify_task(queue_id):
    try:
        settings = Settings.all().get()
        client = TwilioRestClient(settings.account_sid, settings.auth_code)
        call_queue = CallQueue.get_by_id(queue_id)
        next_entry = call_queue.entries.filter('status =', 'P').order('sequence').get()

        if next_entry and call_queue.status == 'P':

            if next_entry.entry_type == 'phone':
                next_entry.status = 'C'
                next_entry.put()
                client.calls.create(to=next_entry.recipient.phone_number, from_=settings.twilio_number,
                                    url='http://%s.appspot.com/call?call_queue_id=%s&queue_entry_id=%s' % (
                                        app_identity.get_application_id(), queue_id, next_entry.key().id()),
                                    status_callback='http://%s.appspot.com/callStatus?call_queue_id=%s&queue_entry_id=%s' % (
                                        app_identity.get_application_id(), queue_id, next_entry.key().id()),
                                    method='GET', timeout=15)
                event_log = 'Called %s (%s)' % (next_entry.recipient.name, next_entry.recipient.phone_number)
                alert_trace = AlertTrace(event_log=event_log, call_queue=call_queue)
                alert_trace.put()

            if next_entry.entry_type == 'sms':
                next_entry.status = 'C'
                next_entry.put()
                sms_message = '%s Send 1 to accept' % call_queue.notifier.sms_message
                client.sms.messages.create(to=next_entry.recipient.phone_number,
                                           from_=settings.twilio_number,
                                           body=sms_message)
                deferred.defer(notify_task, queue_id, _countdown=settings.sms_timeout)
                event_log = 'SMS %s (%s)' % (next_entry.recipient.name, next_entry.recipient.phone_number)
                alert_trace = AlertTrace(event_log=event_log, call_queue=call_queue)
                alert_trace.put()

        elif next_entry == None and call_queue.status == 'P' and call_queue.loop_count <= 5:
            call_queue.loop_count = call_queue.loop_count + 1
            call_queue.put()
            event_log = 'Alert not accepted yet. Restarting. Loop count:%s' % call_queue.loop_count
            alert_trace = AlertTrace(event_log=event_log, call_queue=call_queue)
            alert_trace.put()
            # re start the alert propagation
            entries = []
            for entry in call_queue.entries:
                new_entry = clone_entity(entry, status='P')
                entries.append(new_entry)
            if len(entries) != 0:
                db.put(entries)
                deferred.defer(notify_task, queue_id, _countdown=5 * 60)

    except:
        # raise
        logging.error("notify_task failed for  queue_id : %s" % queue_id)
        logging.error(traceback.format_exc())


def create_notify_queue_task(notifier, email_body):
    recipients = notifier.recipients.order('created')
    if recipients.count() != 0:
        call_queue = CallQueue(status='P', notifier=notifier, email_body=email_body, loop_count=0)
        call_queue.put()
        alert_trace = AlertTrace(event_log='Alert created', call_queue=call_queue)
        alert_trace.put()

    else:
        logging.info("No recipients ...")
        return
    seq = 0
    entries = []
    for recipient in recipients:
        logging.info("Phone Type: %s phone_alert_enabled : %s sms_enabled : %s" % (
            recipient.phone_type, notifier.phone_alert_enabled, notifier.sms_enabled))
        if recipient.phone_type == 'mobile':

            if notifier.sms_enabled:
                seq = seq + 1
                call_entry = CallQueueEntry(sequence=seq, status='P',
                                            entry_type='sms', call_queue=call_queue, recipient=recipient,
                                            phone_number=recipient.phone_number)
                entries.append(call_entry)

            if notifier.phone_alert_enabled:
                seq = seq + 1
                call_entry = CallQueueEntry(sequence=seq, status='P',
                                            entry_type='phone', call_queue=call_queue, recipient=recipient,
                                            phone_number=recipient.phone_number)
                entries.append(call_entry)

        else:
            if notifier.phone_alert_enabled:
                seq = seq + 1
                call_entry = CallQueueEntry(sequence=seq, status='P',
                                            entry_type='phone', call_queue=call_queue, recipient=recipient,
                                            phone_number=recipient.phone_number)
                entries.append(call_entry)
    if len(entries) != 0:
        db.put(entries)
        deferred.defer(notify_task, call_queue.key().id())
        
        

        
            
            
            
    
    
    
        
            
            
            
            
            
        










        

    
    
    