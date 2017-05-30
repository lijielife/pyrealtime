import threading
import multiprocessing
from datetime import datetime, timedelta

from pyrealtime.layer_manager import LayerManager


class Port(object):
    def __init__(self):
        self.out_queues = []

    def get_output(self):
        ctx = multiprocessing.get_context('spawn')
        out_queue = ctx.Queue()
        self.out_queues.append(out_queue)
        return out_queue

    def handle_output(self, data):
        if data is not None:
            for queue in self.out_queues:
                queue.put(data)


class BaseOutputLayer(object):
    def __init__(self, *args, **kwargs):
        self.out_port = Port()

    def handle_output(self, data):
        self.out_port.handle_output(data)

    def get_output(self):
        return self.out_port.get_output()


class BaseInputLayer(object):

    def get_input(self):
        raise NotImplementedError


class BaseLayer(BaseInputLayer, BaseOutputLayer):

    def __init__(self, signal_in=None, name="", *args, **kwargs):
        super().__init__(self, *args, **kwargs)
        self.name = name
        self.signal = None
        self.is_first = True
        self.stop_event = None
        self.set_signal_in(signal_in)

    def post_init(self, data):
        pass

    def start(self, stop_event):
        self.stop_event = stop_event

    def transform(self, data):
        return data

    def initialize(self):
        pass

    def set_signal_in(self, signal_in):
        self.signal_in = signal_in.get_output() if signal_in is not None else None

    def get_signal(self):
        self.signal = None
        if self.signal_in is not None:
            while not self.signal_in.empty():
                self.signal = self.signal_in.get()
                self.handle_signal(self.signal)

    def handle_signal(self, signal):
        pass

    def process_loop(self):
        while not self.stop_event.is_set():
            data = self.get_input()
            if data is None:
                continue
            self.get_signal()
            if self.is_first:
                self.post_init(data)
                self.is_first = False
            data_transformed = self.transform(data)
            if data_transformed is None:
                continue
            self.handle_output(data_transformed)

    def join(self):
        raise NotImplementedError


class ThreadLayer(BaseLayer):
    def __init__(self, parent_proc=None, *args, **kwargs):
        # print("thread layer init")
        super().__init__(*args, **kwargs)
        if parent_proc is not None:
            self.thread = parent_proc.register_child_thread(self)
        else:
            self.create_thread()
            LayerManager.add_layer(self)

    def create_thread(self):
        self.thread = threading.Thread(target=self.run_thread)
        self.thread.daemon = True

    def run_thread(self):
        self.initialize()
        self.process_loop()

    def start(self, *args, **kwargs):
        super(ThreadLayer, self).start(*args, **kwargs)
        self.thread.start()

    def join(self):
        self.thread.join()


class ProcessLayer(BaseLayer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ctx = multiprocessing.get_context('spawn')
        self.process = ctx.Process(target=self.run_proc)
        self.thread_layers = []
        LayerManager.add_layer(self)

    def run_proc(self):
        self.init_child_threads()
        self.initialize()
        t = threading.Thread(target=self.process_loop)
        t.daemon = False
        t.start()

        for thread_layer in self.thread_layers:
            thread_layer.create_thread()
            thread_layer.start(stop_event=self.stop_event)

        self.main_thread_post_init()

    def main_thread_post_init(self):
        pass

    def start(self, *args, **kwargs):
        super(ProcessLayer, self).start(*args, **kwargs)
        self.process.start()

    def join(self):
        self.process.join()

    def init_child_threads(self):
        for thread_layer in self.thread_layers:
            thread_layer.create_thread()

    def register_child_thread(self, thread_layer):
        self.thread_layers.append(thread_layer)


class MultiOutputMixin(BaseOutputLayer):
    def __init__(self, *args, **kwargs):
        self.ports = {}
        self.auto_ports = {}
        super().__init__(*args, **kwargs)

    def get_port(self, port):
        if port in self.ports:
            return self.ports[port]
        if port in self.auto_ports:
            return self.auto_ports[port]
        self._register_port(port, auto=True)
        if port in self.auto_ports:
            return self.auto_ports[port]
        raise NameError("Port %s does not exist" % port)

    def _register_port(self, port, auto=False):
        port_list = self.ports if auto is False else self.auto_ports
        if port in port_list:
            raise NameError("Port %s already exists" % port)
        port_list[port] = Port()

    def handle_output(self, data):
        for key in list(self.ports.keys()) + list(self.auto_ports.keys()):
            if key in self.ports:
                port = self.ports[key]
            elif key in self.auto_ports:
                port = self.auto_ports[key]
            else:
                raise NameError("Port %s does not exist" % key)
            if key in data:
                port.handle_output(data[key])
        super().handle_output(data)


class FPSMixin:
    def __init__(self, time_window=timedelta(seconds=5), *args, **kwargs):
        super().__init__(print_fps=False, *args, **kwargs)
        self.count = 0
        self.start_time = None
        self.reset()
        self.time_window = time_window
        self.print_fps = False
        self.fps = 0

    def tick(self):
        t = datetime.now()
        self.count += 1
        if t - self.start_time >= self.time_window:
            self.fps = self.count / (t - self.start_time).total_seconds()
            if self.print_fps:
                print(self.fps)
            self.reset()

    def reset(self):
        self.count = 0
        self.start_time = datetime.now()


class ProducerMixin(FPSMixin, BaseInputLayer):
    def get_input(self):
        raise NotImplementedError


class TransformMixin(BaseInputLayer):
    def __init__(self, port_in, *args, **kwargs):
        self.port_in = port_in.get_output() if port_in is not None else None
        super().__init__(*args, **kwargs)

    def get_input(self):
        # print("%d: Blocking for input" % threading.get_ident())
        data = self.port_in.get()  # TODO: Handle None
        return data

