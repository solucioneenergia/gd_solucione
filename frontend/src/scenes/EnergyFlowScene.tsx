import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { OperationalStatus } from "../api/qtBridge";

type Props = {
  status: OperationalStatus;
  reduceMotion: boolean;
};

const statusColors: Record<OperationalStatus, number> = {
  idle: 0x5f7590,
  cdp_connected: 0x40d6ae,
  reading_portal: 0x55a8ff,
  downloading: 0x8b7cff,
  processing_pdf: 0xffbd59,
  updating_excel: 0x60d394,
  archiving: 0xe782ff,
  completed: 0x4de3a5,
  error: 0xff5d73,
};

export function EnergyFlowScene({ status, reduceMotion }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({ status, reduceMotion });
  stateRef.current = { status, reduceMotion };

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 2.5, 7.2);
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      host.classList.add("energy-scene-fallback");
      return () => host.classList.remove("energy-scene-fallback");
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    host.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0x9ec7ff, 1.4));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(4, 6, 4);
    scene.add(key);

    const group = new THREE.Group();
    scene.add(group);
    const panelMaterial = new THREE.MeshStandardMaterial({
      color: 0x153b64,
      metalness: 0.35,
      roughness: 0.28,
    });
    const panel = new THREE.Mesh(
      new THREE.BoxGeometry(3.7, 0.12, 2.2),
      panelMaterial,
    );
    panel.rotation.x = -0.32;
    panel.position.set(-1.2, 0.35, 0);
    group.add(panel);

    const grid = new THREE.GridHelper(3.5, 8, 0x4ea1d8, 0x315d83);
    grid.rotation.x = Math.PI / 2 - 0.32;
    grid.position.set(-1.2, 0.42, -0.02);
    group.add(grid);

    const inverterMaterial = new THREE.MeshStandardMaterial({
      color: 0xe7eef9,
      metalness: 0.1,
      roughness: 0.5,
    });
    const inverter = new THREE.Mesh(
      new THREE.BoxGeometry(1.25, 1.65, 0.55),
      inverterMaterial,
    );
    inverter.position.set(2.05, 0.7, 0);
    group.add(inverter);

    const flowCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0.1, 0.55, 0.2),
      new THREE.Vector3(0.8, 1.25, 0.3),
      new THREE.Vector3(1.35, 1.1, 0.2),
      new THREE.Vector3(1.7, 0.75, 0.1),
    ]);
    const points = Array.from({ length: 24 }, (_, index) =>
      flowCurve.getPoint(index / 23),
    );
    const particles = new THREE.Points(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.PointsMaterial({
        color: statusColors[status],
        size: 0.11,
        transparent: true,
        opacity: 0.9,
      }),
    );
    scene.add(particles);

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    let frame = 0;
    let animationId = 0;
    const animate = () => {
      const current = stateRef.current;
      const material = particles.material as THREE.PointsMaterial;
      material.color.setHex(statusColors[current.status]);
      if (!current.reduceMotion) {
        group.rotation.y = Math.sin(frame * 0.004) * 0.12;
        particles.rotation.z -= 0.012;
        material.opacity = 0.7 + Math.sin(frame * 0.045) * 0.2;
      } else {
        group.rotation.y = 0;
        particles.rotation.z = 0;
        material.opacity = 0.75;
      }
      frame += 1;
      renderer.render(scene, camera);
      animationId = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(animationId);
      observer.disconnect();
      renderer.dispose();
      panel.geometry.dispose();
      panelMaterial.dispose();
      inverter.geometry.dispose();
      inverterMaterial.dispose();
      particles.geometry.dispose();
      (particles.material as THREE.Material).dispose();
      renderer.domElement.remove();
    };
  }, []);

  return <div className="energy-scene" ref={hostRef} aria-hidden="true" />;
}
