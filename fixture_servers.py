"""Test-only listener lifecycle; production polling and deadlines stay unchanged."""
import threading


def start_server(case, server):
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()

    def close():
        server.shutdown()
        server.server_close()
        thread.join(5)
        case.assertFalse(thread.is_alive(), 'Test HTTP server failed to stop')

    case.addCleanup(close)
    return thread
