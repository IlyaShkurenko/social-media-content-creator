.PHONY: test-bdd

test-bdd:
	.venv/bin/python -m pytest -q test/bdd

.PHONY: motion-install motion-test motion-render motion-create motion-verify
motion-install:
	cd motion && npm ci --ignore-scripts
	cd motion && node --input-type=module -e 'import {ensureBrowser} from "@remotion/renderer"; console.log(await ensureBrowser({chromeMode:"headless-shell"}));'

motion-test:
	.venv/bin/python -m pytest -q test/services/test_motion_composition.py test/services/test_motion_agent.py test/services/test_motion_authors.py test/services/test_motion_tools.py test/bdd/steps/test_motion_agent_steps.py

motion-render:
	.venv/bin/python -m app.services.creative.motion "$(PROJECT)" "$(OUTPUT)"

motion-verify:
	.venv/bin/python -m app.services.creative.motion "$(OUTPUT)" --verify

motion-create:
	.venv/bin/python -m app.services.creative.motion_agent "$(BRIEF)" "$(CATALOG)" "$(OUTPUT)" --confirm-paid "$(CONFIRM_PAID)" $(MOTION_ARGS)
