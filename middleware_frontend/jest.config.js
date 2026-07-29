/**
 * Jest config for the Electron main-process side of this project
 * (middleware_frontend/electron/). Deliberately scoped to that directory
 * only — separate from, and not wired into, the React app's own tooling
 * (react-scripts / CRA's Jest setup under src/).
 */
module.exports = {
  testEnvironment: "node",
  testMatch: ["<rootDir>/electron/**/*.test.js"],
  testPathIgnorePatterns: ["/node_modules/", "<rootDir>/src/", "<rootDir>/build/"],
};
