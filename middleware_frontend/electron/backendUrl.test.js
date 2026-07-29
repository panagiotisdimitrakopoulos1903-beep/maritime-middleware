/**
 * Tests for resolveBackendUrl() (electron/backendUrl.js).
 *
 * Covers the three precedence cases (env var > config file > default) plus
 * the file-handling edge cases (missing file, corrupt JSON, missing field,
 * non-ENOENT read error) — all via mocking, no real filesystem, no
 * packaged app, no env -i.
 */
jest.mock("electron", () => ({
  app: { getPath: jest.fn(() => "/fake/userdata/path") },
}));
jest.mock("fs");

const fs = require("fs");
const { resolveBackendUrl, DEFAULT_BACKEND_URL } = require("./backendUrl");

describe("resolveBackendUrl", () => {
  let logSpy;
  let warnSpy;

  beforeEach(() => {
    delete process.env.BACKEND_URL;
    jest.clearAllMocks();
    logSpy = jest.spyOn(console, "log").mockImplementation(() => {});
    warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    logSpy.mockRestore();
    warnSpy.mockRestore();
  });

  test("env var wins and short-circuits before touching the filesystem", () => {
    process.env.BACKEND_URL = "http://env-value:9999";

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: "http://env-value:9999", source: "env var" });
    expect(fs.readFileSync).not.toHaveBeenCalled();
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });

  test("config file wins when no env var is set and the file has a valid backendUrl", () => {
    fs.readFileSync.mockReturnValue('{"backendUrl":"http://config-value:1234"}');

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: "http://config-value:1234", source: "config file" });
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });

  test("falls back to default and creates the file when config.json is missing (ENOENT)", () => {
    const enoentErr = Object.assign(new Error("no such file"), { code: "ENOENT" });
    fs.readFileSync.mockImplementation(() => {
      throw enoentErr;
    });

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: DEFAULT_BACKEND_URL, source: "default" });
    expect(fs.writeFileSync).toHaveBeenCalledTimes(1);
    const [writtenPath, writtenContent] = fs.writeFileSync.mock.calls[0];
    expect(writtenPath).toBe("/fake/userdata/path/config.json");
    expect(JSON.parse(writtenContent)).toEqual({ backendUrl: DEFAULT_BACKEND_URL });
  });

  test("falls back to default and leaves the file untouched on corrupt JSON", () => {
    fs.readFileSync.mockReturnValue("not valid json{{{");

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: DEFAULT_BACKEND_URL, source: "default" });
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });

  test("falls back to default and leaves the file untouched when backendUrl field is missing", () => {
    fs.readFileSync.mockReturnValue('{"somethingElse": true}');

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: DEFAULT_BACKEND_URL, source: "default" });
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });

  test("falls back to default and leaves the file untouched when backendUrl field is an empty string", () => {
    fs.readFileSync.mockReturnValue('{"backendUrl": ""}');

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: DEFAULT_BACKEND_URL, source: "default" });
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });

  test("falls back to default without attempting a write on a non-ENOENT read error (e.g. permissions)", () => {
    const permErr = Object.assign(new Error("permission denied"), { code: "EACCES" });
    fs.readFileSync.mockImplementation(() => {
      throw permErr;
    });

    const result = resolveBackendUrl();

    expect(result).toEqual({ url: DEFAULT_BACKEND_URL, source: "default" });
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });
});
