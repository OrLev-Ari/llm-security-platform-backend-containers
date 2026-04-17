here is the manual to setup the needed ec2 to run the system. note that to run the verifier we need atleast 15 gb of memory in this current lower version

instance type should be m7i-flex.large

Notice that following the huggingface token masking improvement when deploying the ec2 instance you do need to pull the hf token from the ssm, if you don't have it there you should manually insert it and use the secret management.docx in the repo to see how to pull it and use a global instance of it.