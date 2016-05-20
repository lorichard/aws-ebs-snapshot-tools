import json
import boto3
import datetime
import collections
import copy

def do_tagging(ec2, toTag, retentionDays):
    for retention_days in toTag.keys():
        delete_date = datetime.date.today() + datetime.timedelta(days=retentionDays)
        delete_fmt = delete_date.strftime('%Y-%m-%d')
        print "Will delete %d snapshots on %s" % (len(toTag[retentionDays]), delete_fmt)
        ec2.create_tags(
            Resources=toTag[retentionDays],
            Tags=[
                {'Key': 'DeleteOn', 'Value': delete_fmt},
            ]
        )

def do_snapshot(ec2,instance, volume, time, desc):
    snap =volume.create_snapshot(Description=desc + instance.id + ", volume id: " + volume.volume_id +" taken on " + time )
    return snap

def lambda_handler(event, context):
    contextVariables = context.invoked_function_arn
    contextVariables = contextVariables.split(":")
    region = contextVariables[3]
    accountId = contextVariables[4]
    ddsnsArn="arn:aws:sns:"+region+":"+accountId+":ebs-snapshot-to-datadog"
    snsArn="arn:aws:sns:"+region+":"+accountId+":ebs-snapshot-"+region
    
    ec2 = boto3.resource('ec2', region_name=region)
    sqs = boto3.resource('sqs')
    sns = boto3.client('sns')
    today = unicode(datetime.date.today())
    desc = "EBS Snapshot of the instance: "
    toTag = collections.defaultdict(list)
    retentionDays = 14    
    queueName = 'ebs-snapshots-'+region
    queue = sqs.get_queue_by_name(QueueName=queueName)

    try:    
        instances=[]
        for x in xrange(400):
            messages = queue.receive_messages()
            for m in messages:
                instances.append(m.body)
                m.delete()
                
        numberInstances = len(instances)
        leftoverInstances = copy.deepcopy(instances)
        print instances
        print "There are : " + str(numberInstances) + " snapshots to be created"
        
        for i in instances:
            instance = ec2.Instance(i)
            volumes_iterator = instance.volumes
            volumes = list(volumes_iterator.all())

            #snapshot each volume
            for v in volumes:
                snap = do_snapshot(ec2,instance,v,today,desc)
                toTag[retentionDays].append(snap.id)
                print "snapshot has been created for instance: " + instance.id + " of the volume: " + v.volume_id

            leftoverInstances.remove(i) 
            
        do_tagging(ec2,toTag, retentionDays)
        print leftoverInstances
        if len(leftoverInstances) == 0:
            print "There is nothing leftover to snapshot"
            snsData = {}
            snsData['status'] = "success"
            snsData['time'] = str(datetime.datetime.now())
            snsData['msg'] = "ebs-snapshot-queue-handler - " + str(numberInstances) + " snapshots were created."
            snsData['instances'] = instances

            #publish a SNS message that will notify datadog of the success 
            response = sns.publish(
                TargetArn=ddsnsArn,
                Message=json.dumps(snsData))
            
    except Exception as e:
        print(e)
        print("There are leftover snapshots: ")
        print leftoverInstances
        print "Sending leftover instances to SQS"
        for i in leftoverInstances:
            queue.send_message(MessageBody=i)
        snsData = {}
        snsData['status']="failure"
        snsData['time'] = str(datetime.datetime.now())
        snsData['msg']="There are " + len(leftoverInstances) + " instances left to be snapshotted"
        snsData['instances'] = leftoverInstances
        #publish a SNS message that will notify datadog of the failure 
        ddresponse = sns.publish(
            TargetArn=ddsnsArn,
            Message=json.dumps(snsData))
        #publish a SNS message that will trigger the ebs-snapshot-queue function 
        response = sns.publish(
            TargetArn=snsArn,
            Message=json.dumps(snsData))
        raise e