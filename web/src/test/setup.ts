// Vitest setup: jsdom lacks EventSource; the SPA only needs it to exist.
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener() {}

  removeEventListener() {}

  close() {}
}

(globalThis as unknown as { EventSource: unknown }).EventSource =
  FakeEventSource;
