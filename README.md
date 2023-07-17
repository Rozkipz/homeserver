https://averagelinuxuser.com/ssh-into-virtualbox/
https://0to1.nl/post/k3s-kubectl-permission/
https://www.truenas.com/community/threads/use-intel-gpu-hardware-encoding-in-plex-kubernetes-deployment.89747/
https://bjordanov.com/install-guest-additions-virtual-machine-vm-virtualbox/

# Nvidia k3s support
https://docs.k3s.io/advanced#nvidia-container-runtime-support

# Intel support
https://intel.github.io/intel-device-plugins-for-kubernetes/cmd/gpu_plugin/README.html#pre-built-images

--cluster-cidr when starting k3s


curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode --cluster-cidr=10.0.24.0/24

config file - /etc/rancher/k3s/k3s.yaml

---------VM---------
su -
apt update
apt install sudo curl git build-essential dkms linux-headers-$(uname -r) -y

<**Insert Guest Additions CD image**>
<**install https://download.virtualbox.org/virtualbox/7.0.8/Oracle_VM_VirtualBox_Extension_Pack-7.0.8.vbox-extpack**>
^ <** go to https://download.virtualbox.org/virtualbox/7.0.8/ and download  VBoxGuestAdditions_7.0.8.iso**>
mkdir /tmp/test
sudo mount /dev/cdrom /tmp/test
sudo /tmp/test/VBoxLinuxAdditions.run --nox11

usermod -aG sudo rowan
<log out + log in>
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
sudo reboot
#sudo k3s server

systemctl stop k3s
sudo k3s certificate rotate
systemctl start k3s


wget https://github.com/derailed/k9s/releases/download/v0.27.4/k9s_Linux_amd64.tar.gz
tar -xvf k9s_Linux_amd64.tar.gz
sudo mv k9s /usr/local/bin/
sudo ln /etc/rancher/k3s/k3s.yaml ~/.kube/config
k9s
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash


kubectl apply -k https://github.com/intel/intel-device-plugins-for-kubernetes/deployments/gpu_plugin?ref=v0.27.1
kubectl get nodes -o=jsonpath="{range .items[*]}{.metadata.name}{'\n'}{' i915: '}{.status.allocatable.gpu\.intel\.com/i915}{'\n'}" <---- should see 



helm install plex .





kubectl port-forward plex/plex-6ccd4bc477-zsrb5 --namespace=plex --address='0.0.0.0' 32400:32400

http://127.0.0.1:32400/manage


```shell
rowan@k3s:~$ kubectl  get svc --all-namespaces
NAMESPACE     NAME             TYPE           CLUSTER-IP      EXTERNAL-IP   PORT(S)                      AGE
default       kubernetes       ClusterIP      10.43.0.1       <none>        443/TCP                      45m
kube-system   kube-dns         ClusterIP      10.43.0.10      <none>        53/UDP,53/TCP,9153/TCP       45m
kube-system   metrics-server   ClusterIP      10.43.234.244   <none>        443/TCP                      45m
kube-system   traefik          LoadBalancer   10.43.23.63     10.0.2.15     80:30646/TCP,443:32539/TCP   44m
rowan@k3s:~$ curl -I http://10.0.2.15:30646
HTTP/1.1 404 Not Found
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
Date: Fri, 14 Jul 2023 23:36:23 GMT
Content-Length: 19

rowan@k3s:~$ 
```
