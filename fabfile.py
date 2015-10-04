import os
from fabric.api import env, sudo, put, task, cd
from fabric.operations import prompt
import boto
import boto.ec2
import boto.logs
import time
import conf
from datetime import datetime

conf.set_fabric_env()

@task
def create_instance(name, tag=None):
    """
    Launch an instance and wait for it to start running.
    Returns a tuple consisting of the Instance object and the CmdShell
    object, if request, or None.
    tag        A name that will be used to tag the instance so we can
               easily find it later.
    """
    print("Started creating {}...".format(name))

    conn = connect_to_ec2()

    reservation = conn.run_instances(
            image_id=env.aws_ami_id,
            key_name=env.aws_ssh_key_name,
            instance_type=env.aws_instance_type,
            security_groups=[env.aws_security_group_name])


    instance = reservation.instances[0]
    conn.create_tags([instance.id], {"Name": name})
    if tag:
        instance.add_tag(tag)
    while instance.state != 'running':
        time.sleep(2)
        instance.update()

    print("Instance state: %s" % instance.state)
    print("Public dns: %s" % instance.public_dns_name)
    return instance.public_dns_name


@task
def terminate_instance(name):
    """
    Terminates all servers with the given name
    """

    print("Started terminating {}...".format(name))

    conn = connect_to_ec2()
    filters = {"tag:Name": name}
    for reservation in conn.get_all_instances(filters=filters):
        for instance in reservation.instances:
            if "terminated" in str(instance._state):
                print "instance {} is already terminated".format(instance.id)
                continue
            else:
                print instance._state
            print (instance.id, instance.tags['Name'])
            if prompt("terminate? (y/n) ").lower() == "y":
                print("Terminating {}".format(instance.id))
                conn.terminate_instances(instance_ids=[instance.id])
                print("Terminated")


@task
def install_solr(name):
    """
    Install SOLR on a machine
    """

    set_host_by_name_tag(name)

    sudo('yum -y install wget')  # To download solr packages
    sudo('yum -y install java-1.8.0-openjdk-devel.x86_64')  # Latest java
    sudo('yum -y install lsof.x86_64')  # Requirement by solr to check if already running

    # download and unpack solr
    with cd('/tmp/'):
        sudo('wget apache.spd.co.il/lucene/solr/5.3.0/solr-5.3.0.tgz')
        sudo('tar zxf solr-5.3.0.tgz --directory /opt')
        sudo('rm solr-5.3.0.tgz')

    # deploy systemd solr startup script
    with cd('/etc/systemd/system/'):
        put('solr.service', 'solr.service', use_sudo=True)
        sudo('systemctl enable solr')
        sudo('systemctl start solr')


@task
def ec2_cleanup():
    """
    Shutting-down all instances which correspond to following conditions:
    1. not tagged with the "dont-touch" tag
    2. older than 1 week
    3. not spot instances
    """
    print("Started cleanup...")

    for region_name in env.aws_active_regions:
        # create CloudWatch log stream
        log_writer = create_cloudwatch_logstream(
            region_name=region_name,
            log_stream_name='ec2_cleanup')

        conn = connect_to_ec2(region_name)

        instances_to_cleanup = find_instances_to_cleanup(conn)
        if not instances_to_cleanup:
            print 'No instances to clean'
            return
        instances_ids_to_cleanup = [x.id for x in instances_to_cleanup]

        # stopping all instances
        conn.stop_instances(instance_ids=instances_ids_to_cleanup)

        for instance in instances_to_cleanup:
            delete_instance_volumes(conn, instance)

            # writing to CloudWatch log
            print "terminating instance {}.".format(instance.id)
            log_writer = write_cloudwatch_logstream(
                region_name=region_name,
                log_stream_name=log_writer['log_stream_name'],
                message='terminating instance {}'.format(instance.id),
                log_stream_token=log_writer['sequence_token'])

        # terminating all instance
        conn.terminate_instances(instance_ids=instances_ids_to_cleanup)


#  ----------HELPER FUNCTIONS-----------


def set_host_by_name_tag(nametag):
    conn = connect_to_ec2()
    filters = {"tag:Name": nametag}
    for reservation in conn.get_all_instances(filters=filters):
        for instance in reservation.instances:
            env.host_string = instance.ip_address
            env.port = env.aws_ssh_port
            env.user = env.aws_linux_user_name
            env.key_filename = env.aws_ssh_key_path


def get_epoch_timestamp():
    return (datetime.utcnow() - datetime(1970, 1, 1)).total_seconds() * 1000


def create_cloudwatch_logstream(region_name, log_stream_name):
        logs = boto.logs.connect_to_region(region_name)
        log_stream_name = "{}_{}_utc".format(log_stream_name, datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S'))
        logs.create_log_stream(log_group_name=env.aws_logs_group_name,
                               log_stream_name=log_stream_name)

        logstream_res = logs.put_log_events(
                               log_group_name=env.aws_logs_group_name,
                               log_stream_name=log_stream_name,
                               log_events=[{'timestamp': get_epoch_timestamp(),
                                            'message': 'created logstream {}'.format(log_stream_name)}])
        return {'log_stream_name': log_stream_name,
                'sequence_token': logstream_res['nextSequenceToken']}


def write_cloudwatch_logstream(region_name, log_stream_name, message, log_stream_token):
        logs = boto.logs.connect_to_region(region_name)
        logstream_res = logs.put_log_events(
                               log_group_name=env.aws_logs_group_name,
                               log_stream_name=log_stream_name,
                               log_events=[{'timestamp': get_epoch_timestamp(),
                                            'message': message}],
                               sequence_token=log_stream_token)
        return {'log_stream_name': log_stream_name,
                'sequence_token': logstream_res['nextSequenceToken']}


def connect_to_ec2(aws_region_name=env.aws_default_region):
    conn = boto.ec2.connect_to_region(aws_region_name,
                                      aws_access_key_id=env.aws_access_key_id,
                                      aws_secret_access_key=env.aws_secret_access_key)
    return conn


def find_instances_to_cleanup(ec2_connection):
    instances_to_cleanup = []
    for reservation in ec2_connection.get_all_instances():
        for instance in reservation.instances:
            # validating conditions for instance termination
            is_spot_instance = instance.spot_instance_request_id is not None
            is_not_running = instance.state != 'running'
            is_dont_touch_tag = 'dont-touch' in instance.tags
            if is_spot_instance or is_not_running or is_dont_touch_tag:
                continue

            # checking uptime and terminating instances
            instance_launch_time = datetime.strptime(instance.launch_time, '%Y-%m-%dT%H:%M:%S.000Z')
            instance_uptime_days = (datetime.utcnow() - instance_launch_time).days
            if env.aws_instance_uptime_days_limit < instance_uptime_days:
                # adding instance to cleanup list
                instances_to_cleanup.append(instance)
    return instances_to_cleanup


def delete_instance_volumes(ec2_connection, instance):
    # waiting for instance to stop before can detach and delete volume
    while instance.state != 'stopped':
        time.sleep(3)
        instance.update()

    # detaching and deleting all instance EBS volumes
    attached_volumes = ec2_connection.get_all_volumes(filters={'attachment.instance-id': instance.id})
    for volume in attached_volumes:
        volume.detach()
        while volume.status != 'available':
            time.sleep(1)
            volume.update()
        volume.delete()
