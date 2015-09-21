import errno
import os
import json
from fabric.api import env, sudo, run, task, settings
import boto
import boto.ec2
import time
import conf

# -----SETTINGS-----------


for name, value in conf.deploy_settings.items():
    env_value = os.getenv(name.upper())
    env_value = value
    env[name] = env_value
    if not env_value:
        raise Exception("Please make sure to enter your AWS keys/info in your deploy/environment file before running fab scripts. {} is current set to {}".format(name, value))

# Define non-configurable settings.
env.root_directory = os.path.dirname(os.path.realpath(__file__))
env.deploy_directory = os.path.join(env.root_directory, 'deploy')
env.app_settings_file = os.path.join(env.deploy_directory, 'settings.json')
env.ssh_directory = os.path.join(env.deploy_directory, 'ssh')
env.fab_hosts_directory = os.path.join(env.deploy_directory, 'fab_hosts')


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
    prep_path(env.ssh_directory)
    prep_path(env.fab_hosts_directory)


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

    host_data = {
        'host_string': instance.ip_address,
        'port': env.aws_ssh_port,
        'user': env.aws_linux_user_name,
        'key_filename': env.aws_ssh_key_path,
    }
    with open(os.path.join(env.ssh_directory, ''.join([name, '.json'])), 'w') as f:
        json.dump(host_data, f)

    f = open("deploy/fab_hosts/{}.txt".format(name), "w")
    f.write(instance.public_dns_name)
    f.close()
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
            if raw_input("terminate? (y/n) ").lower() == "y":
                print("Terminating {}".format(instance.id))
                conn.terminate_instances(instance_ids=[instance.id])
                os.remove(os.path.join(env.ssh_directory, ''.join([name, '.json'])))  # noqa
                print("Terminated")


@task
def install_solr(name):
    """SSH into an instance."""
    with open(os.path.join(env.ssh_directory, ''.join([name, '.json'])), 'r') as f:  # noqa
        host_data = json.load(f)
    f = open("deploy/fab_hosts/{}.txt".format(name))
    env.host_string = "{}@{}".format(env.aws_linux_user_name, f.readline().strip())
    with settings(**host_data):
        sudo('yum -y install wget')
        sudo('yum -y install java-1.8.0-openjdk-devel.x86_64')
        sudo('yum -y install lsof.x86_64')
        run('wget apache.spd.co.il/lucene/solr/5.3.0/solr-5.3.0.tgz')
        run('tar zxf solr-5.3.0.tgz')
        run('rm solr-5.3.0.tgz')
        run('solr-5.3.0/bin/solr start -p 8983')


#  ----------HELPER FUNCTIONS-----------


def prep_path(directory):
    try:
        os.makedirs(directory)
    except OSError as exception:
        if exception.errno == errno.EEXIST and os.path.isdir(directory):
            pass
        else:
            raise