# cython: language_level=3, boundscheck=False, wraparound=False
"""Helpers SOCKS5 acelerados com Cython (hot paths).

``protocols/socks5.py`` tenta importar daqui e cai para a implementação
em Python puro se a extensão não estiver compilada.
"""


cpdef bytes pack_port(int port):
    """Converte a porta para 2 bytes big-endian (network byte order)."""
    cdef unsigned char buf[2]
    if port < 0 or port > 65535:
        raise ValueError("porta inválida")
    buf[0] = (port >> 8) & 0xFF
    buf[1] = port & 0xFF
    return buf[:2]
