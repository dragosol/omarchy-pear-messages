# Restricting BlueBubbles to Tailscale (optional, on the Mac)

BlueBubbles always listens on every network interface. If the Mac is on Tailscale you can make it
answer only over Tailscale with a packet-filter anchor.

Everything below runs **on the Mac**, not on the computer running Pear Messages. Pear Messages
needs no elevated privileges on either machine and never applies these rules for you.

```
# /etc/pf.anchors/bluebubbles-tailscale
pass in quick on lo0 proto tcp to port 1234
pass in quick inet  proto tcp from 100.64.0.0/10 to port 1234
pass in quick inet6 proto tcp from fd7a:115c:a1e0::/48 to port 1234
block drop in quick proto tcp to port 1234
```

Load it, as an administrator on the Mac:

```
pfctl -a com.apple/250.bluebubbles -f /etc/pf.anchors/bluebubbles-tailscale && pfctl -E
```
