#!/usr/bin/expect -f

set timeout 15
set mount_point "$env(HOME)/R640-NAS"
set webdav_url "https://files.re8ch.com:18444/"

file mkdir $mount_point

if {![catch {exec /sbin/mount} mounts]} {
  if {[string first " on $mount_point (" $mounts] >= 0} {
    exit 0
  }
}

if {[catch {
  exec /usr/bin/security find-internet-password \
    -s files.re8ch.com -a woomoo -P 18444 -r htps -w
} password]} {
  exit 1
}

# Interactive mode reads from the pseudo-terminal. The password comes from
# Keychain and is never stored in this file or passed as a process argument.
spawn /sbin/mount_webdav -i -s -v R640-NAS $webdav_url $mount_point
expect {
  "Username:" {
    send -- "woomoo\r"
    exp_continue
  }
  "Password:" {
    send -- "$password\r"
    exp_continue
  }
  eof {
    catch wait result
    exit [lindex $result 3]
  }
  timeout {
    close
    catch wait
    exit 1
  }
}
