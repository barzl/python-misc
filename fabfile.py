import os
from fabric.api import env, sudo, put, task, cd
from fabric.operations import prompt
import boto
import boto.ec2
import time
import conf


conf.set_fabric_env()

# Define non-configurable settings.
env.root_directory = os.path.dirname(os.path.realpath(__file__))
env.deploy_directory = os.path.join(env.root_directory, 'deploy')
env.app_settings_file = os.path.join(env.deploy_directory, 'settings.json')


def connect_to_ec2():
    conn = boto.ec2.connect_to_region(env.aws_default_region,
                                      aws_access_key_id=env.aws_access_key_id,
                                      aws_secret_access_key=env.aws_secret_access_key)
    return conn


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
    print("...Creating EC2 instance...")

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
        print("Instance state: %s" % instance.state)
        time.sleep(15)
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
