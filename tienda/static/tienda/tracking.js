/**
 * Rastreo GPS en Tiempo Real (Estándar 25s + WebSocket + Leaflet OpenStreetMap)
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
  let pingTimer = null;
  let reconnectTimer = null;
  let estadoActual = cfg.estadoInicial || {};

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
          opacity: 0.8,
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
      map.fitBounds(L.latLngBounds(puntos), { padding: [42, 42], maxZoom: 16 });
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
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);
      setTimeout(function () {
        map.invalidateSize();
      }, 180);
    }
    aplicarEstadoSeguimiento(datos, true);
  }

  function aplicarEstadoSeguimiento(datos, ajustarVista) {
    if (!datos || !map) return;
    estadoActual = Object.assign({}, estadoActual, datos);

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

    const dir = datos.direccion_entrega || estadoActual.direccion_entrega;
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
      if (elRepartidorNombre && rep.nombre_completo) {
        elRepartidorNombre.textContent =
          rep.nombre_completo + (rep.telefono ? " · " + rep.telefono : "");
      }
      const lat = rep.latitud != null ? Number(rep.latitud) : suc && suc.latitud != null ? Number(suc.latitud) : null;
      const lng = rep.longitud != null ? Number(rep.longitud) : suc && suc.longitud != null ? Number(suc.longitud) : null;
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
    const pos = [Number(telemetria.latitud), Number(telemetria.longitud)];
    const rumbo = telemetria.rumbo != null ? Number(telemetria.rumbo) : 0;

    if (!markerRepartidor) {
      markerRepartidor = L.marker(pos, {
        icon: crearIconoPin("moto", "🛵", rumbo),
        zIndexOffset: 500,
      })
        .addTo(map)
        .bindPopup("<strong>Repartidor en ruta</strong>");
    } else {
      markerRepartidor.setIcon(crearIconoPin("moto", "🛵", rumbo));
      animarMarcador(markerRepartidor, pos, 850);
    }

    if (elVelocidad) {
      if (telemetria.velocidad != null && !isNaN(Number(telemetria.velocidad))) {
        const v = Number(telemetria.velocidad);
        const kmh = v <= 35 ? (v * 3.6).toFixed(1) : v.toFixed(1);
        elVelocidad.textContent = `${kmh} km/h`;
      } else {
        elVelocidad.textContent = "En movimiento";
      }
    }
    if (elRumbo) {
      elRumbo.textContent =
        telemetria.rumbo != null && !isNaN(Number(telemetria.rumbo))
          ? `${Math.round(Number(telemetria.rumbo))}°`
          : "—";
    }
    if (elActualizacion && telemetria.ultima_actualizacion) {
      elActualizacion.textContent = formatearHora(telemetria.ultima_actualizacion);
    }
    actualizarLineaRuta();
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
      .catch(function () {
        // El mapa ya está renderizado con los datos iniciales de PostgreSQL
      });
  }

  function conectarWebSocket() {
    if (!cfg.wsBase || !cfg.jwtToken) {
      actualizarBadgeWs("Mapa activo (sin token WS)", "pendiente");
      return;
    }
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const baseWs = cfg.wsBase.replace(/\/$/, "");
    const wsUrl = `${baseWs}/pedidos/${cfg.idPedido}/ws/seguimiento?token=${encodeURIComponent(
      cfg.jwtToken
    )}`;

    actualizarBadgeWs("Conectando GPS...", "pendiente");
    try {
      ws = new WebSocket(wsUrl);
    } catch (_) {
      actualizarBadgeWs("Modo local (REST)", "pendiente");
      return;
    }

    ws.onopen = function () {
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
          if (elEstadoPedidoBadge && msg.estado) {
            elEstadoPedidoBadge.textContent = msg.estado;
            elEstadoPedidoBadge.className =
              "badge " + String(msg.estado).toLowerCase() + " text-sm px-3.5 py-1.5";
          }
        } else if (msg.evento === "pago_confirmado") {
          if (elEstadoPagoBadge) {
            elEstadoPagoBadge.textContent = "Pagado";
            elEstadoPagoBadge.className = "badge aprobado";
          }
          if (elMetodoPagoTexto && msg.metodo_pago) {
            elMetodoPagoTexto.textContent =
              msg.metodo_pago === "TARJETA" || msg.metodo_pago === "WOMPI"
                ? "Tarjeta / Wompi"
                : msg.metodo_pago;
          }
        }
      } catch (_) {}
    };

    ws.onclose = function () {
      if (pingTimer) clearInterval(pingTimer);
      const estadoPed = (cfg.estadoPedido || "").toUpperCase();
      if (estadoPed !== "ENTREGADO" && estadoPed !== "CANCELADO") {
        actualizarBadgeWs("Reconectando señal...", "pendiente");
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(conectarWebSocket, 5000);
      } else {
        actualizarBadgeWs("Entrega finalizada", "");
      }
    };

    ws.onerror = function () {
      actualizarBadgeWs("Mapa activo (sin señal WS)", "pendiente");
    };
  }

  if (btnCentrar) {
    btnCentrar.addEventListener("click", function () {
      centrarMapa();
    });
  }

  inicializarMapa(cfg.estadoInicial || {});
  cargarRestSeguimiento();
  conectarWebSocket();
})();