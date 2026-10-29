"""MOCK/CPU deployment tests: no real GPU, API, service, model or EDA call."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve import gpu_binding as gb
from serve import deployment_profile as dp

VISIBLE = {'pci':'0000:43:00.0','render_node':'/dev/dri/renderD130'}
BUS = json.dumps({'card3':{'PCI Bus':'0000:43:00.0'}, 'card0':{'PCI Bus':'0000:11:00.0'}})


class BindingTests(unittest.TestCase):
    def test_pci_binding_does_not_assume_hip_zero_or_render_number(self):
        self.assertEqual(gb.select_counter(VISIBLE, BUS)['physical_counter'], '3')

    def test_real_readonly_metadata_shape(self):
        result = gb.select_counter(VISIBLE, '{"card0":{"PCI Bus":"0000:43:00.0"}}')
        self.assertEqual(result['physical_counter'], '0')
        self.assertEqual(result['method'], 'EXPOSED_RENDER_PCI_AND_ROCM_SMI_BUS')

    def test_pci_domain_normalization(self):
        self.assertEqual(gb.select_counter(VISIBLE, '{"card2":{"PCI Bus":"00000000:43:00.0"}}')['physical_counter'], '2')

    def test_unmatched_gpu_is_not_guessed(self):
        with self.assertRaises(RuntimeError):gb.select_counter(VISIBLE, '{"card0":{"PCI Bus":"0000:11:00.0"}}')

    def test_duplicate_counter_json_is_rejected(self):
        with self.assertRaises(ValueError):gb.select_counter(VISIBLE, '{"card0":{},"card0":{"PCI Bus":"0000:43:00.0"}}')

    def test_ambiguous_physical_match_is_rejected(self):
        with self.assertRaises(RuntimeError):gb.select_counter(VISIBLE, '{"card0":{"PCI Bus":"0000:43:00.0"},"card2":{"PCI Bus":"0000:43:00.0"}}')

    def test_malformed_bus_or_pci_is_rejected(self):
        for raw in ('not JSON','[]','{"card0":{"PCI Bus":true}}','{"card0":{"PCI Bus":"unknown"}}'):
            with self.subTest(raw=raw),self.assertRaises((ValueError,RuntimeError)):gb.select_counter(VISIBLE,raw)

    def test_multiple_pci_fields_are_rejected(self):
        with self.assertRaises(ValueError):gb.select_counter(VISIBLE, '{"card0":{"PCI Bus":"0000:43:00.0","PCI Bus ID":"0000:43:00.0"}}')

    def test_explicit_verified_mapping_is_preserved_without_query(self):
        with patch.object(gb,'visible_amd_pci',side_effect=AssertionError('no discovery')),patch.object(gb.subprocess,'run',side_effect=AssertionError('no query')):
            self.assertEqual(gb.resolve_counter({'AGENT_INFERENCE_GPU':'2'})['physical_counter'],'2')

    def test_invalid_explicit_mapping_is_not_silently_replaced(self):
        for bad in ('','-1','0,1','True','\uff10'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):gb.resolve_counter({'AGENT_INFERENCE_GPU':bad})

    def test_multidevice_or_unknown_visible_selector_abstains(self):
        for env in ({'HIP_VISIBLE_DEVICES':'0,1'},{'HIP_VISIBLE_DEVICES':'1'},{'ROCR_VISIBLE_DEVICES':'GPU-unknown'}):
            with self.subTest(env=env),self.assertRaises(RuntimeError):gb.resolve_counter(env)

    def test_hip_zero_alone_is_not_physical_proof(self):
        with patch.object(gb,'visible_amd_pci',side_effect=RuntimeError('no exposed device')),patch.object(gb.subprocess,'run') as query:
            with self.assertRaises(RuntimeError):gb.resolve_counter({'HIP_VISIBLE_DEVICES':'0'})
            query.assert_not_called()

    def test_only_documented_readonly_bus_query_is_used(self):
        with patch.object(gb,'visible_amd_pci',return_value=VISIBLE),patch.object(gb.subprocess,'run',return_value=subprocess.CompletedProcess([],0,BUS,'')) as query:
            result=gb.resolve_counter({'HIP_VISIBLE_DEVICES':'0'},timeout=2)
            self.assertEqual(result['physical_counter'],'3')
            self.assertEqual(query.call_args.args[0],['rocm-smi','--showbus','--json'])
            self.assertEqual(query.call_args.kwargs['timeout'],2)

    def test_failed_bus_query_is_not_ready(self):
        with patch.object(gb,'visible_amd_pci',return_value=VISIBLE),patch.object(gb.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','error')):
            with self.assertRaises(RuntimeError):gb.resolve_counter({})

    def test_query_timeout_propagates_without_retry(self):
        with patch.object(gb,'visible_amd_pci',return_value=VISIBLE),patch.object(gb.subprocess,'run',side_effect=subprocess.TimeoutExpired('MOCK bus query',1)) as query:
            with self.assertRaises(subprocess.TimeoutExpired):gb.resolve_counter({},timeout=1)
            self.assertEqual(query.call_count,1)

    def test_nonpositive_binding_budget_stops(self):
        with patch.object(gb,'visible_amd_pci',side_effect=AssertionError('no access')):
            with self.assertRaises(TimeoutError):gb.resolve_counter({},timeout=0)

    def test_visible_discovery_requires_one_amd_character_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);dev=base/'dev';sysfs=base/'sys';dev.mkdir();sysfs.mkdir()
            for name in ('renderD128','renderD130'):
                (dev/name).touch();(sysfs/name/'device').mkdir(parents=True)
                (sysfs/name/'device/vendor').write_text('0x1002')
            with patch.object(gb.stat,'S_ISCHR',return_value=True),patch.object(gb,'_pci',return_value='0000:43:00.0'):
                with self.assertRaises(RuntimeError):gb.visible_amd_pci(dev,sysfs)

    def test_regular_file_cannot_impersonate_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'renderD130').touch()
            with self.assertRaises(RuntimeError):gb.visible_amd_pci(base,base)

    def test_auto_binding_is_inherited_by_memory_probe(self):
        binding={'physical_counter':'3','method':'MOCK_PCI_MATCH'}
        def memory(timeout):
            self.assertEqual(os.environ['AGENT_INFERENCE_GPU'],'3')
            self.assertGreater(timeout,0);self.assertLessEqual(timeout,2)
            return {'status':'MEASURED','used_bytes':28_000_000_000,'total_bytes':48_000_000_000}
        with patch.dict(os.environ,{'HIP_VISIBLE_DEVICES':'0'},clear=True),patch.object(gb,'resolve_counter',return_value=binding),patch('rtl_agent.gpu_measurement.probe_vram',side_effect=memory):
            result=dp.measured_memory(timeout=2)
            self.assertEqual(result['physical_binding'],binding)
            self.assertEqual(dp.validate_ready_memory(result)['used_bytes'],28_000_000_000)

    def test_binding_time_consumes_existing_memory_deadline(self):
        with patch.dict(os.environ,{},clear=True),patch.object(dp.time,'monotonic',side_effect=[100,100,100,103]),patch.object(gb,'resolve_counter',return_value={'physical_counter':'3'}),patch('rtl_agent.gpu_measurement.probe_vram') as probe:
            with self.assertRaises(TimeoutError):dp.measured_memory(timeout=2)
            probe.assert_not_called()

    def test_binding_lock_wait_is_bounded_by_original_timeout(self):
        class Lock:
            def acquire(inner,timeout):
                self.assertGreaterEqual(timeout,0);self.assertLessEqual(timeout,0.1)
                return False
            def release(inner):raise AssertionError('Unacquired lock must not be released')
        with patch.object(dp,'_BINDING_LOCK',Lock()),patch.object(gb,'resolve_counter') as resolve:
            with self.assertRaises(TimeoutError):dp.measured_memory(timeout=0.1)
            resolve.assert_not_called()


class PackagingTests(unittest.TestCase):
    def _shell(self,overrides):
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        self.assertTrue(bash.is_file(),'Existing Git Bash required; no skip')
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'read_profile.sh'
            script.write_bytes(b'source "$1"\nprintf "%s\\n" "$MODEL_NAME" "$XILINXD_LICENSE_FILE" "$FLEXLM_TIMEOUT" "$AGENT_TMP" "$EDA_TMP" "$GPU_MEMORY_UTILIZATION"\n')
            env={k:v for k,v in os.environ.items() if k not in ('MODEL_NAME','XILINXD_LICENSE_FILE','FLEXLM_TIMEOUT','AGENT_TMP','EDA_TMP','GPU_MEMORY_UTILIZATION')}
            env.update(overrides)
            p=subprocess.run([str(bash),str(script),str(ROOT/'serve/profile.sh')],capture_output=True,text=True,env=env,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr)
            return p.stdout.splitlines()

    def test_profile_sets_documented_license_and_local_scratch(self):
        fields=self._shell({})
        self.assertEqual(fields,['codev-r1-7b','2100@vivado-lic.fpgachina.svc.cluster.local','300000000','/var/tmp','/var/tmp/eda','0.55'])

    def test_operator_overrides_are_not_replaced(self):
        fields=self._shell({'XILINXD_LICENSE_FILE':'MOCK_LOCAL_LICENSE','FLEXLM_TIMEOUT':'123','GPU_MEMORY_UTILIZATION':'0.55'})
        self.assertEqual(fields[1:3],['MOCK_LOCAL_LICENSE','123'])

    def test_official_base_is_pinned_and_license_survives_direct_fallback(self):
        docker=(ROOT/'Dockerfile').read_text()
        self.assertIn('ARG BASE_IMAGE=fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56',docker)
        self.assertIn('XILINXD_LICENSE_FILE=2100@vivado-lic.fpgachina.svc.cluster.local',docker)
        self.assertIn('FLEXLM_TIMEOUT=300000000',docker)
        self.assertNotIn('COPY .',docker.replace('# Explicit whitelist. No COPY . and no development data enters the image.',''))

    def test_core_agent_baseline_skill_budgets_and_api_are_byte_preserved(self):
        binding=json.loads((ROOT/'docs/SOURCE_BINDING.json').read_text())
        changed={'Dockerfile','serve/deployment_profile.py','serve/profile.sh'}
        for name,expected in binding['historical_runtime_files_sha256'].items():
            if name not in changed:
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),expected,name)

    def test_report_is_anonymous_and_amd_notice_retained(self):
        report=(ROOT/'REPORT.md').read_text()
        self.assertNotIn('44611',report)
        notice=(ROOT/'THIRD_PARTY_NOTICES.md').read_text()
        self.assertIn('Copyright (c) 2026 AMD',notice)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"',notice)


if __name__=='__main__':
    unittest.main(verbosity=2)
