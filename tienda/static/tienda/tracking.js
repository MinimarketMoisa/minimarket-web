/**
 * Rastreo GPS en Tiempo Real (Estándar 25s + WebSocket + Sincronización BD + Leaflet)
 * Portal Web Administrativo y de Sucursales — MiniMarket
 */
(function () {
  "use strict";

  const cfg = window.MM_TRACKING_CONFIG;
  if (!cfg || !cfg.idPedido) return;

  const mapContainer = document.getElementById("mm-tracking-map");
  if (!mapContainer || typeof L === "undefined") return;

  const elWsBadge = document.getElementById("mm-ws-status");
  const elRepartidorNombre = document.getElementById("mm-trk-repartidor");
  const elVelocidad = document.getElementById("mm-trk-velocidad");
  const elRumbo = document.getElementById("mm-trk-rumbo");
  const elActualizacion = document.getElementById("mm-trk-actualizacion");
  const elEstadoPedidoBadge = document.getElementById("mm-pedido-estado-badge");
  const elEstadoPagoBadge = document.getElementById("mm-pedido-pago-badge");
  const elMetodoPagoTexto = document.getElementById("mm-pedido-metodo-texto");
  const btnCentrar = document.getElementById("mm-btn-centrar-mapa");

  const COORD_DEFAULT = [13.4834, -88.1833]; // San Miguel, El Salvador
  let map = null;
  let markerSucursal = null;
  let markerDestino = null;
  let markerRepartidor = null;
  let polylineRuta = null;
  let animFrame = null;
  let ws = null;
  let wsConectado = false;
  let pingTimer = null;
  let reconnectTimer = null;
  let pollTimer = null;
  let estadoActual = cfg.estadoInicial || {};
  let ultimaPosicionConocida = null;

  const ETIQUETAS_ESTADO = {
    PENDIENTE: "Pendiente",
    PREPARADO: "Preparado",
    EN_RUTA: "En ruta",
    ENTREGADO: "Entregado",
    CANCELADO: "Cancelado",
  };

  function crearIconoPin(tipo, emoji, rumboGrados) {
    const rotacion = Number.isFinite(Number(rumboGrados)) ? Number(rumboGrados) : 0;
    const flechaHtml =
      tipo === "moto"
        ? `<span class="mm-pin-flecha" style="transform: rotate(${rotacion}deg)">▲</span>`
        : "";
    return L.divIcon({
      className: "mm-leaflet-div-icon",
      html: `<div class="mm-pin mm-pin-${tipo}"><span>${emoji}</span>${flechaHtml}</div>`,
      iconSize: [38, 38],
      iconAnchor: [19, 19],
      popupAnchor: [0, -18],
    });
  }

  function actualizarBadgeWs(texto, variante) {
    if (!elWsBadge) return;
    elWsBadge.textContent = texto;
    elWsBadge.className = "badge " + (variante || "");
  }

  function formatearHora(isoStr) {
    if (!isoStr) return "En espera de señal GPS";
    try {
      const fecha = new Date(isoStr);
      if (isNaN(fecha.getTime())) return String(isoStr);
      return fecha.toLocaleTimeString("es-SV", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch (_) {
      return String(isoStr);
    }
  }

  function calcularRumboGrados(lat1, lon1, lat2, lon2) {
    const toRad = (d) => (d * Math.PI) / 180;
    const toDeg = (r) => (r * 180) / Math.PI;
    const dLon = toRad(lon2 - lon1);
    const y = Math.sin(dLon) * Math.cos(toRad(lat2));
    const x =
      Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
      Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function calcularDistanciaMetros(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(toRad(lat1)) *
        Math.cos(toRad(lat2)) *
        Math.sin(dLon / 2) *
        Math.sin(dLon / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function actualizarEstadoPedidoUI(nuevoEstado, etiquetaDisplay) {
    if (!nuevoEstado) return;
    const codigo = String(nuevoEstado).toUpperCase();
    cfg.estadoPedido = codigo;
    if (elEstadoPedidoBadge) {
      elEstadoPedidoBadge.textContent =
        etiquetaDisplay || ETIQUETAS_ESTADO[codigo] || codigo;
      elEstadoPedidoBadge.className =
        "badge " + codigo.toLowerCase() + " text-sm px-3.5 py-1.5";
    }
  }

  function animarMarcador(marker, destinoLatLng, duracionMs) {
    if (!marker) return;
    if (animFrame) cancelAnimationFrame(animFrame);
    const origen = marker.getLatLng();
    if (!origen) {
      marker.setLatLng(destinoLatLng);
      return;
    }
    const inicio = performance.now();
    const dLat = destinoLatLng[0] - origen.lat;
    const dLng = destinoLatLng[1] - origen.lng;
    if (Math.abs(dLat) < 1e-7 && Math.abs(dLng) < 1e-7) return;

    function paso(ahora) {
      const t = Math.min(1, (ahora - inicio) / (duracionMs || 850));
      const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
      marker.setLatLng([origen.lat + dLat * ease, origen.lng + dLng * ease]);
      actualizarLineaRuta();
      if (t < 1) {
        animFrame = requestAnimationFrame(paso);
      }
    }
    animFrame = requestAnimationFrame(paso);
  }

  function actualizarLineaRuta() {
    if (!map) return;
    const puntos = [];
    if (markerSucursal) puntos.push(markerSucursal.getLatLng());
    if (markerRepartidor) puntos.push(markerRepartidor.getLatLng());
    if (markerDestino) puntos.push(markerDestino.getLatLng());

    if (puntos.length >= 2) {
      if (!polylineRuta) {
        polylineRuta = L.polyline(puntos, {
          color: "#f67300",
          weight: 4,
          opacity: 0.85,
          dashArray: "8, 8",
        }).addTo(map);
      } else {
        polylineRuta.setLatLngs(puntos);
      }
    }
  }

  function centrarMapa() {
    if (!map) return;
    const puntos = [];
    if (markerSucursal) puntos.push(markerSucursal.getLatLng());
    if (markerDestino) puntos.push(markerDestino.getLatLng());
    if (markerRepartidor) puntos.push(markerRepartidor.getLatLng());
    if (puntos.length >= 2) {
      map.fitBounds(L.latLngBounds(puntos), { padding: [46, 46], maxZoom: 16 });
    } else if (puntos.length === 1) {
      map.setView(puntos[0], 15);
    }
  }

  function inicializarMapa(datos) {
    if (!map) {
      map = L.map(mapContainer, {
        center: COORD_DEFAULT,
        zoom: 14,
        scrollWheelZoom: false,
      });
      // Servidor de teselas Esri ArcGIS World Street Map (libre de bloqueos y sin marcas de agua de API Key)
      L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 19,
          attribution:
            "Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, Intermap, iPC, NRCAN, METI, TomTom",
        }
      ).addTo(map);
      setTimeout(function () {
        map.invalidateSize();
      }, 180);
    }
    aplicarEstadoSeguimiento(datos, true);
  }

  function aplicarEstadoSeguimiento(datos, ajustarVista) {
    if (!datos || !map) return;
    estadoActual = Object.assign({}, estadoActual, datos);

    if (datos.estado) {
      actualizarEstadoPedidoUI(datos.estado, datos.estado_display);
    }
    if (datos.estado_pago && elEstadoPagoBadge) {
      elEstadoPagoBadge.textContent =
        datos.estado_pago_display ||
        (datos.estado_pago === "PAGADO" ? "Pagado" : "Pendiente");
      elEstadoPagoBadge.className =
        "badge " + (datos.estado_pago === "PAGADO" ? "aprobado" : "pendiente");
    }
    if (datos.metodo_pago_display && elMetodoPagoTexto) {
      elMetodoPagoTexto.textContent = datos.metodo_pago_display;
    }

    const suc = datos.sucursal || estadoActual.sucursal;
    if (suc && suc.latitud != null && suc.longitud != null) {
      const posSuc = [Number(suc.latitud), Number(suc.longitud)];
      if (!markerSucursal) {
        markerSucursal = L.marker(posSuc, {
          icon: crearIconoPin("tienda", "🏪", 0),
        })
          .addTo(map)
          .bindPopup(
            `<strong>Sucursal: ${suc.nombre || "MiniMarket"}</strong><br>${suc.direccion || ""}`
          );
      } else {
        markerSucursal.setLatLng(posSuc);
      }
    }

    const dir =
      datos.direccion_entrega ||
      datos.destino ||
      estadoActual.direccion_entrega ||
      estadoActual.destino;
    if (dir && dir.latitud != null && dir.longitud != null) {
      const posDir = [Number(dir.latitud), Number(dir.longitud)];
      if (!markerDestino) {
        markerDestino = L.marker(posDir, {
          icon: crearIconoPin("casa", "🏠", 0),
        })
          .addTo(map)
          .bindPopup(
            `<strong>Entrega al cliente</strong><br>${dir.detalle_direccion || ""}${
              dir.punto_referencia ? "<br><em>Ref: " + dir.punto_referencia + "</em>" : ""
            }`
          );
      } else {
        markerDestino.setLatLng(posDir);
      }
    }

    const rep = datos.repartidor || estadoActual.repartidor;
    if (rep) {
      const nombreRep = rep.nombre_completo || rep.nombre;
      if (elRepartidorNombre && nombreRep) {
        elRepartidorNombre.textContent =
          nombreRep + (rep.telefono ? " · " + rep.telefono : "");
      }
      const lat =
        rep.latitud != null
          ? Number(rep.latitud)
          : suc && suc.latitud != null
          ? Number(suc.latitud)
          : null;
      const lng =
        rep.longitud != null
          ? Number(rep.longitud)
          : suc && suc.longitud != null
          ? Number(suc.longitud)
          : null;
      if (lat != null && lng != null) {
        actualizarPosicionRepartidor({
          latitud: lat,
          longitud: lng,
          rumbo: rep.rumbo,
          velocidad: rep.velocidad,
          ultima_actualizacion: rep.ultima_actualizacion,
        });
      }
    }

    actualizarLineaRuta();
    if (ajustarVista) {
      centrarMapa();
    }
  }

  function actualizarPosicionRepartidor(telemetria) {
    if (!map || telemetria.latitud == null || telemetria.longitud == null) return;
    const lat = Number(telemetria.latitud);
    const lng = Number(telemetria.longitud);
    const pos = [lat, lng];
    const ahoraMs = Date.now();

    let rumbo = telemetria.rumbo != null ? Number(telemetria.rumbo) : null;
    let velocidadKmh = null;

    if (telemetria.velocidad != null && !isNaN(Number(telemetria.velocidad))) {
      const v = Number(telemetria.velocidad);
      velocidadKmh = v <= 35 ? v * 3.6 : v;
    }

    if (ultimaPosicionConocida) {
      const distM = calcularDistanciaMetros(
        ultimaPosicionConocida.lat,
        ultimaPosicionConocida.lng,
        lat,
        lng
      );
      if (distM > 1.5) {
        if (rumbo == null) {
          rumbo = calcularRumboGrados(
            ultimaPosicionConocida.lat,
            ultimaPosicionConocida.lng,
            lat,
            lng
          );
        }
        if (velocidadKmh == null) {
          const dtSeg = Math.max(1, (ahoraMs - ultimaPosicionConocida.ts) / 1000);
          velocidadKmh = Math.min(95, (distM / dtSeg) * 3.6);
        }
      }
    }

    if (
      !ultimaPosicionConocida ||
      Math.abs(ultimaPosicionConocida.lat - lat) > 1e-7 ||
      Math.abs(ultimaPosicionConocida.lng - lng) > 1e-7
    ) {
      ultimaPosicionConocida = {
        lat: lat,
        lng: lng,
        ts: ahoraMs,
        rumbo: rumbo != null ? rumbo : ultimaPosicionConocida ? ultimaPosicionConocida.rumbo : 0,
      };
    } else if (rumbo == null && ultimaPosicionConocida) {
      rumbo = ultimaPosicionConocida.rumbo;
    }

    const rumboFinal = rumbo != null && !isNaN(rumbo) ? rumbo : 0;

    if (!markerRepartidor) {
      markerRepartidor = L.marker(pos, {
        icon: crearIconoPin("moto", "🛵", rumboFinal),
        zIndexOffset: 500,
      })
        .addTo(map)
        .bindPopup("<strong>Repartidor en ruta</strong>");
    } else {
      markerRepartidor.setIcon(crearIconoPin("moto", "🛵", rumboFinal));
      animarMarcador(markerRepartidor, pos, 850);
    }

    if (elVelocidad) {
      if (velocidadKmh != null && !isNaN(velocidadKmh) && velocidadKmh > 0.2) {
        elVelocidad.textContent = `${velocidadKmh.toFixed(1)} km/h`;
      } else {
        const est = String(cfg.estadoPedido || "").toUpperCase();
        elVelocidad.textContent = est === "EN_RUTA" ? "En ruta activa" : "En posición";
      }
    }
    if (elRumbo) {
      elRumbo.textContent =
        rumbo != null && !isNaN(Number(rumbo)) && Number(rumbo) > 0
          ? `${Math.round(Number(rumbo))}°`
          : "En ruta";
    }
    if (elActualizacion && telemetria.ultima_actualizacion) {
      elActualizacion.textContent = formatearHora(telemetria.ultima_actualizacion);
    }
    actualizarLineaRuta();
  }

  function consultarTelemetriaLocal() {
    const url = cfg.telemetriaUrl || `/pedidos/${cfg.idPedido}/telemetria/`;
    fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (datos) {
        aplicarEstadoSeguimiento(datos, false);
        if (!wsConectado) {
          const estadoPed = String(cfg.estadoPedido || "").toUpperCase();
          if (estadoPed === "ENTREGADO" || estadoPed === "CANCELADO") {
            actualizarBadgeWs("Entrega finalizada", "");
          } else {
            actualizarBadgeWs("● En vivo (GPS activo)", "aprobado");
          }
        }
      })
      .catch(function () {});
  }

  function cargarRestSeguimiento() {
    if (!cfg.apiBase || !cfg.jwtToken) return;
    const url = `${cfg.apiBase.replace(/\/$/, "")}/pedidos/${cfg.idPedido}/seguimiento`;
    fetch(url, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${cfg.jwtToken}`,
        Accept: "application/json",
      },
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (datos) {
        aplicarEstadoSeguimiento(datos, true);
      })
      .catch(function () {});
  }

  function conectarWebSocket() {
    if (!cfg.wsBase || !cfg.jwtToken) {
      consultarTelemetriaLocal();
      return;
    }
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const baseWs = cfg.wsBase.replace(/\/$/, "");
    const wsUrl = `${baseWs}/pedidos/${cfg.idPedido}/ws/seguimiento?token=${encodeURIComponent(
      cfg.jwtToken
    )}`;

    try {
      ws = new WebSocket(wsUrl);
    } catch (_) {
      consultarTelemetriaLocal();
      return;
    }

    ws.onopen = function () {
      wsConectado = true;
      actualizarBadgeWs("● En vivo (25s GPS)", "aprobado");
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send("ping");
        }
      }, 25000);
    };

    ws.onmessage = function (event) {
      if (!event.data || event.data === "pong") return;
      try {
        const msg = JSON.parse(event.data);
        if (msg.evento === "estado_inicial" && msg.datos) {
          aplicarEstadoSeguimiento(msg.datos, false);
        } else if (msg.evento === "posicion_actualizada") {
          actualizarPosicionRepartidor({
            latitud: msg.latitud,
            longitud: msg.longitud,
            rumbo: msg.rumbo,
            velocidad: msg.velocidad,
            ultima_actualizacion: msg.ultima_actualizacion || new Date().toISOString(),
          });
        } else if (msg.evento === "estado_pedido_actualizado") {
          actualizarEstadoPedidoUI(msg.estado);
        } else if (msg.evento === "pago_confirmado") {
          if (elEstadoPagoBadge) {
            elEstadoPagoBadge.textContent = "Pagado";
            elEstadoPagoBadge.className = "badge aprobado";
          }
          if (elMetodoPagoTexto && msg.metodo_pago) {
            elMetodoPagoTexto.textContent =
              msg.metodo_pago === "TARJETA" || msg.metodo_pago === "WOMPI"
                ? "Tarjeta"
                : msg.metodo_pago;
          }
        }
      } catch (_) {}
    };

    ws.onclose = function () {
      wsConectado = false;
      if (pingTimer) clearInterval(pingTimer);
      const estadoPed = String(cfg.estadoPedido || "").toUpperCase();
      if (estadoPed !== "ENTREGADO" && estadoPed !== "CANCELADO") {
        consultarTelemetriaLocal();
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(conectarWebSocket, 10000);
      } else {
        actualizarBadgeWs("Entrega finalizada", "");
      }
    };

    ws.onerror = function () {
      wsConectado = false;
      consultarTelemetriaLocal();
    };
  }

  if (btnCentrar) {
    btnCentrar.addEventListener("click", function () {
      centrarMapa();
    });
  }

  inicializarMapa(cfg.estadoInicial || {});
  consultarTelemetriaLocal();
  cargarRestSeguimiento();
  conectarWebSocket();

  // Sondeo continuo contra PostgreSQL cada 5 segundos como respaldo en tiempo real
  pollTimer = setInterval(function () {
    const estadoPed = String(cfg.estadoPedido || "").toUpperCase();
    if (estadoPed === "ENTREGADO" || estadoPed === "CANCELADO") {
      clearInterval(pollTimer);
      return;
    }
    consultarTelemetriaLocal();
  }, 5000);
})();
