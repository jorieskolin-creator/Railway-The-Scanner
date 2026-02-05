
// App and ReactDOM are global
const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const { createRoot } = (window as any).ReactDOM;
const { StrictMode } = window.React;
const App = (window as any).App;

const root = createRoot(rootElement);
root.render(
  <StrictMode>
    <App />
  </StrictMode>
);
