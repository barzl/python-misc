# python-misc
Playing with fabric
This fabfile has following tasks:

1. Create new amazon instance via: __fab create_instance:*instance-name*__
2. Terminate instance: __fab terminate_instance:*instance-name*__
3. install solr on existing instance: __fab install_solr:*instance-name*__
4. clear machined with uptime more than 1 week: __fab ec2_cleanup__
