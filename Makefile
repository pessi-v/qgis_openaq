VERSION := $(shell grep '^version=' metadata.txt | cut -d= -f2)
ZIP     := qgis_openaq_v$(VERSION).zip

.PHONY: zip clean

zip:
	@cd .. && git -C qgis_openaq archive \
		--prefix=qgis_openaq/ \
		--format=zip HEAD \
		> $(ZIP)
	@echo "Built ../$(ZIP)"

clean:
	@rm -f ../qgis_openaq_v*.zip
