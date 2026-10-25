{ config, ... }:

{
  # Temporary Perkons-only route for private pilot acceptance. Remove this module
  # and rebuild before testing the public DNS cutover.
  networking.hosts."127.0.0.1" = [ "app.runahealth.io" ];

  systemd.sockets.runa-private-route = {
    description = "Private Runa HTTPS route through the operator's SSM tunnel";
    wantedBy = [ ];
    listenStreams = [ "127.0.0.1:443" ];
    socketConfig.Accept = false;
  };

  systemd.services.runa-private-route = {
    description = "Forward private Runa HTTPS to the existing loopback SSM tunnel";
    wantedBy = [ ];
    requires = [ "runa-private-route.socket" ];
    after = [ "runa-private-route.socket" ];
    partOf = [ "runa-private-route.socket" ];
    serviceConfig = {
      Type = "notify";
      ExecStart = "${config.systemd.package}/lib/systemd/systemd-socket-proxyd 127.0.0.1:8443";
      DynamicUser = true;
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_UNIX"
      ];
      IPAddressDeny = "any";
      IPAddressAllow = "127.0.0.1/32";
      LimitCORE = 0;
    };
  };
}
